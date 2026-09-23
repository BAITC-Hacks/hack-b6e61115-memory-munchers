using System.ComponentModel.DataAnnotations;
using MemoryMunchers.Agents;
using MemoryMunchers.Shopping;
using Microsoft.AspNetCore.Mvc;

namespace MemoryMunchers.Controllers;

[ApiController, Route("api/product-chat/sessions")]
public sealed class ProductChatController(IAgentSessionService sessions, IAgentRunner runner,
    ProductChatService chat, AttachmentService attachments, AgentEventSink events, ILogger<ProductChatController> logger) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> List(CancellationToken token) => Ok(await chat.ListAsync(token));

    [HttpPost]
    public async Task<IActionResult> Create(CancellationToken token)
    {
        var session = await sessions.CreateAsync(ProductConsultant.Id, "Консультация по товарам", cancellationToken: token);
        return Ok(new { session.Id });
    }

    [HttpGet("{id:guid}")]
    public async Task<IActionResult> Get(Guid id, CancellationToken token) => Ok(await chat.GetAsync(id, token));

    [HttpPost("{id:guid}/messages")]
    public async Task<IActionResult> Message(Guid id, ChatMessageRequest request, CancellationToken token)
    {
        await attachments.RequireSessionAsync(id, token);
        if (request.ClientRequestId == Guid.Empty) return BadRequest(new { detail = "Укажите ID запроса." });
        var input = new AgentRunInput(request.Message, request.ClientRequestId, request.AttachmentIds, request.ProductId);
        if (!Request.Headers.Accept.Any(h => h?.Contains("text/event-stream") == true))
        {
            await runner.RunAsync(id, input, token);
            return Ok(await chat.GetAsync(id, token));
        }
        Response.ContentType = "text/event-stream";
        Response.Headers.CacheControl = "no-cache, no-store";
        Response.Headers["X-Accel-Buffering"] = "no";
        events.Write = async (kind, payload, ct) =>
        {
            await Response.WriteAsync($"event: {kind}\ndata: {ShopJson.Write(payload)}\n\n", ct);
            await Response.Body.FlushAsync(ct);
        };
        try
        {
            await events.PublishAsync("status", new { message = "Проверяю запрос…", resetText = true }, token);
            await runner.RunAsync(id, input, token);
            await events.PublishAsync("completed", await chat.GetAsync(id, token), token);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (AgentException error)
        {
            await events.PublishAsync("error", new { code = error.Code, message = error.Message }, token);
        }
        catch (Exception error)
        {
            logger.LogError(error, "Product chat request failed for session {SessionId}", id);
            await events.PublishAsync("error", new { code = "chat_failed", message = "Не удалось завершить запрос. Обновите диалог перед повторной отправкой." }, token);
        }
        finally { events.Write = null; }
        return new EmptyResult();
    }

    [HttpPost("{id:guid}/attachments"), RequestSizeLimit(6 * 1024 * 1024), RequestFormLimits(MultipartBodyLengthLimit = 6 * 1024 * 1024)]
    public async Task<IActionResult> Upload(Guid id, IFormFile file, CancellationToken token) => Ok(await attachments.UploadAsync(id, file, token));
}

public sealed record ChatMessageRequest([Required, StringLength(8000, MinimumLength = 1)] string Message,
    Guid ClientRequestId, [MaxLength(3)] Guid[]? AttachmentIds = null, [Range(1, int.MaxValue)] int? ProductId = null);
