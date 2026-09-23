using System.Text.Json;
using MemoryMunchers.Agents.Persistence;
using Microsoft.Extensions.Options;

namespace MemoryMunchers.Agents;

public sealed class AgentRunner(IAgentSessionStore store, IAgentSessionLock sessionLock,
    IAgentToolRegistry registry, IAgentModelClient model, IOptions<AgentOptions> options,
    TimeProvider clock, ILogger<AgentRunner> logger) : IAgentRunner
{
    public async Task<AgentRunResult> RunAsync(Guid sessionId, string message,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(message) || message.Length > 32_000)
            throw new AgentException("invalid_message", "A message between 1 and 32000 characters is required.", 400);

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(options.Value.RunTimeoutSeconds));
        var token = timeout.Token;
        await using var lease = await sessionLock.TryAcquireAsync(sessionId, token)
            ?? throw new AgentException("session_busy", "This session already has an active run.", 409);
        var session = await store.FindAsync(sessionId, token)
            ?? throw new AgentException("session_not_found", "The agent session was not found.", 404);
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

        var run = new AgentRun { SessionId = sessionId, Input = message, StartedAt = clock.GetUtcNow() };
        session.Runs.Add(run);
        history.Add(AgentHistory.UserMessage(message));

        try
        {
            while (run.ModelCalls < session.MaxModelCalls)
            {
                token.ThrowIfCancellationRequested();
                run.ModelCalls++;
                await CheckpointAsync(token);
                var response = await model.RespondAsync(new AgentModelRequest(session.Model, session.Instructions,
                    history.ToArray(), tools.Select(tool => tool.Definition).ToArray()), token);

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
                    var output = await ExecuteToolAsync(call, allowedTools, new(sessionId, run.Id), token);
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
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { throw; }
        catch (Exception exception)
        {
            logger.LogWarning(exception, "Tool {ToolName} failed in run {RunId}", call.Name, context.RunId);
            return Error("tool_failed", "The tool failed. Do not assume the operation succeeded.");
        }
    }

    private static string Error(string code, string message) => JsonSerializer.Serialize(new { error = code, message });
}
