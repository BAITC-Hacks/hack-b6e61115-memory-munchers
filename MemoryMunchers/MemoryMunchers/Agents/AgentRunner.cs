using System.Text.Json;
using MemoryMunchers.Agents.Persistence;
using Microsoft.Extensions.Options;
using MemoryMunchers.Shopping;
using System.Security.Cryptography;
using System.Text;

namespace MemoryMunchers.Agents;

public sealed class AgentRunner(IAgentSessionStore store, IAgentSessionLock sessionLock,
    IAgentToolRegistry registry, IAgentModelClient model, IOptions<AgentOptions> options,
    TimeProvider clock, ILogger<AgentRunner> logger, AttachmentService attachments, BasketService basket,
    IOptions<ShoppingOptions> shopping, AgentEventSink events) : IAgentRunner
{
    public Task<AgentRunResult> RunAsync(Guid sessionId, string message, CancellationToken cancellationToken = default) =>
        RunAsync(sessionId, new AgentRunInput(message), cancellationToken);

    public async Task<AgentRunResult> RunAsync(Guid sessionId, AgentRunInput input,
        CancellationToken cancellationToken = default)
    {
        var message = input.Message;
        if (string.IsNullOrWhiteSpace(message) || message.Length > 32_000)
            throw new AgentException("invalid_message", "A message between 1 and 32000 characters is required.", 400);

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(options.Value.RunTimeoutSeconds));
        var token = timeout.Token;
        await using var lease = await sessionLock.TryAcquireAsync(sessionId, token)
            ?? throw new AgentException("session_busy", "This session already has an active run.", 409);
        var session = await store.FindAsync(sessionId, token)
            ?? throw new AgentException("session_not_found", "The agent session was not found.", 404);
        var consultant = session.AgentId == ProductConsultant.Id;
        if (consultant) timeout.CancelAfter(TimeSpan.FromSeconds(shopping.Value.RunTimeoutSeconds));
        var requestHash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(ShopJson.Write(input))));
        if (input.ClientRequestId.HasValue)
        {
            var existing = session.Runs.SingleOrDefault(r => r.ClientRequestId == input.ClientRequestId);
            if (existing != null)
            {
                if (existing.RequestHash != requestHash) throw new AgentException("request_conflict", "Этот ID уже использован для другого сообщения.", 409);
                if (existing.Status == AgentRunStatus.Completed) return existing.ToResult();
                if (existing.Status == AgentRunStatus.Running)
                {
                    existing.Status = AgentRunStatus.Failed;
                    existing.ErrorCode = "run_interrupted";
                    existing.ErrorMessage = "Предыдущий запрос был прерван. Обновите диалог перед повторной отправкой.";
                    existing.CompletedAt = clock.GetUtcNow();
                    await store.SaveAsync(token);
                }
                throw new AgentException(existing.ErrorCode ?? "run_failed", existing.ErrorMessage ?? "Запрос завершился ошибкой. Отправьте новое сообщение.", 409);
            }
        }
        var tools = registry.Resolve(JsonSerializer.Deserialize<string[]>(session.ToolNamesJson)!);
        var allowedTools = tools.ToDictionary(tool => tool.Definition.Name, StringComparer.Ordinal);
        var history = JsonSerializer.Deserialize<List<JsonElement>>(session.HistoryJson)!;

        // Acquiring the database lock proves no other runner still owns these unfinished runs.
        foreach (var interrupted in session.Runs.Where(run => run.Status == AgentRunStatus.Running))
        {
            interrupted.Status = AgentRunStatus.Failed;
            interrupted.ErrorCode = "run_interrupted";
            interrupted.ErrorMessage = "The previous process stopped before completing this run.";
            interrupted.CompletedAt = clock.GetUtcNow();
        }
        AgentHistory.ClosePendingToolCalls(history);

        var parts = new List<JsonElement> { JsonSerializer.SerializeToElement(new { type = "input_text", text = message }) };
        if (input.ProductId.HasValue) parts.Add(JsonSerializer.SerializeToElement(new { type = "input_text", text = $"Контекст страницы: выбран товар ID {input.ProductId.Value}. Проверь его через get_product_details." }));
        parts.AddRange(await attachments.PartsAsync(sessionId, input.AttachmentIds ?? [], token));
        var run = new AgentRun { SessionId = sessionId, Input = message, StartedAt = clock.GetUtcNow(),
            ClientRequestId = input.ClientRequestId, RequestHash = requestHash };
        session.Runs.Add(run);
        history.Add(JsonSerializer.SerializeToElement(new { role = "user", content = parts }));

        try
        {
            while (run.ModelCalls < session.MaxModelCalls)
            {
                token.ThrowIfCancellationRequested();
                run.ModelCalls++;
                await CheckpointAsync(token);
                await events.PublishAsync("status", new { message = "Готовлю ответ…", resetText = true }, token);
                var instructions = session.Instructions;
                if (consultant)
                {
                    var currentBasket = await basket.GetAsync(token);
                    instructions += "\nТекущее состояние корзины (данные сервера): " + ShopJson.Write(new { currentBasket.Version,
                        items = currentBasket.Items.Select(i => new { i.Product.Id, i.Product.Name, i.Quantity }) });
                }
                var context = consultant ? RecentHistory(history) : history.ToArray();
                var response = await model.RespondAsync(new AgentModelRequest(session.Model, instructions,
                    await attachments.ResolveAsync(context, token), tools.Select(tool => tool.Definition).ToArray(),
                    consultant ? shopping.Value.MaxOutputTokens : null, consultant ? shopping.Value.ReasoningEffort : null), token);

                history.AddRange(response.OutputItems);
                await CheckpointAsync(token);
                if (response.ToolCalls.Count == 0)
                {
                    if (string.IsNullOrWhiteSpace(response.Text))
                        throw new AgentException("empty_model_response", "The model returned no answer or tool calls.", 502);

                    run.Output = response.Text;
                    run.Status = AgentRunStatus.Completed;
                    run.CompletedAt = clock.GetUtcNow();
                    await CheckpointAsync(token);
                    return run.ToResult();
                }

                if (run.ToolCalls + response.ToolCalls.Count > 32)
                    throw new AgentException("tool_limit_reached", "The run exceeded its limit of 32 tool calls.", 422);

                // Tools share the request's scoped DbContext, so execute them sequentially.
                foreach (var call in response.ToolCalls)
                {
                    token.ThrowIfCancellationRequested();
                    run.ToolCalls++;
                    await events.PublishAsync("status", new { message = call.Name == "prepare_basket_addition" ? "Проверяю количество и готовлю подтверждение…" : "Проверяю данные каталога…", resetText = false }, token);
                    var output = await ExecuteToolAsync(call, allowedTools, new(sessionId, run.Id, call.CallId), token);
                    history.Add(AgentHistory.ToolOutput(call.CallId, output));
                    await CheckpointAsync(token);
                }
            }

            throw new AgentException("model_call_limit_reached", "The agent reached its model call limit without a final answer.", 422);
        }
        catch (Exception exception)
        {
            var failure = exception switch
            {
                AgentException known => known,
                OperationCanceledException when cancellationToken.IsCancellationRequested =>
                    new AgentException("run_cancelled", "The caller cancelled the run.", 499, exception),
                OperationCanceledException => new AgentException("run_timeout", "The agent run timed out.", 504, exception),
                _ => new AgentException("run_failed", "The agent run failed. See server logs for details.", 500, exception)
            };
            run.Status = failure.Code == "run_cancelled" ? AgentRunStatus.Cancelled : AgentRunStatus.Failed;
            run.ErrorCode = failure.Code;
            run.ErrorMessage = failure.Message;
            run.CompletedAt = clock.GetUtcNow();
            AgentHistory.ClosePendingToolCalls(history);

            // A disconnected HTTP client must not prevent recording failure and releasing the session.
            using var cleanup = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            try { await CheckpointAsync(cleanup.Token); }
            catch (Exception saveException) { logger.LogError(saveException, "Could not save failed agent run {RunId}", run.Id); }

            logger.LogWarning(exception, "Agent run {RunId} in session {SessionId} failed ({Code})", run.Id, sessionId, failure.Code);
            if (cancellationToken.IsCancellationRequested && exception is OperationCanceledException)
                throw;
            throw new AgentException(failure.Code, failure.Message, failure.StatusCode, exception)
            { SessionId = sessionId, RunId = run.Id };
        }

        Task CheckpointAsync(CancellationToken checkpointToken)
        {
            session.HistoryJson = JsonSerializer.Serialize(history);
            session.UpdatedAt = clock.GetUtcNow();
            return store.SaveAsync(checkpointToken);
        }
    }

    private async Task<string> ExecuteToolAsync(AgentToolCall call, IReadOnlyDictionary<string, IAgentTool> tools,
        AgentToolContext context, CancellationToken cancellationToken)
    {
        if (!tools.TryGetValue(call.Name, out var tool))
            return Error("tool_not_allowed", "This tool is not enabled for the session.");

        try
        {
            using var arguments = JsonDocument.Parse(call.Arguments);
            if (arguments.RootElement.ValueKind != JsonValueKind.Object)
                return Error("invalid_arguments", "Tool arguments must be a JSON object.");
            return await tool.ExecuteAsync(arguments.RootElement, context, cancellationToken);
        }
        catch (JsonException) { return Error("invalid_arguments", "Tool arguments must be valid JSON matching the tool schema."); }
        catch (AgentToolInputException exception) { return Error("invalid_arguments", exception.Message); }
        catch (AgentException exception) { return Error(exception.Code, exception.Message); }
        catch (Exception exception) when (exception is InvalidOperationException or FormatException or OverflowException)
        { return Error("invalid_arguments", "Tool arguments do not match the schema."); }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { throw; }
        catch (Exception exception)
        {
            logger.LogWarning(exception, "Tool {ToolName} failed in run {RunId}", call.Name, context.RunId);
            return Error("tool_failed", "The tool failed. Do not assume the operation succeeded.");
        }
    }

    private static string Error(string code, string message) => JsonSerializer.Serialize(new { error = code, message });

    private static JsonElement[] RecentHistory(List<JsonElement> history)
    {
        var boundaries = history.Select((item, index) => (item, index))
            .Where(x => x.item.TryGetProperty("role", out var role) && role.GetString() == "user").Select(x => x.index).ToArray();
        // Cut only at complete user-turn boundaries, retaining tool calls and their outputs together.
        return history.Skip(boundaries.Length > 12 ? boundaries[^12] : 0).ToArray();
    }
}
