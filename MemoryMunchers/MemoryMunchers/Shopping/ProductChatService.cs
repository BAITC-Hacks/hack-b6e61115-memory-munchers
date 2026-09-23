using System.Text.Json;
using MemoryMunchers.Agents;
using MemoryMunchers.Persistence;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Shopping;

public sealed class ProductChatService(IAgentSessionService sessions, AttachmentService attachments,
    MemoryMunchersDbContext db, BasketService basket)
{
    public async Task<object> GetAsync(Guid id, CancellationToken token)
    {
        await attachments.RequireSessionAsync(id, token);
        var session = await sessions.GetAsync(id, token);
        var userMessages = session.History.Where(item => item.TryGetProperty("role", out var role) && role.GetString() == "user").ToArray();
        var messages = session.Runs.SelectMany((run, index) =>
        {
            var user = index < userMessages.Length ? userMessages[index] : default;
            var entries = new List<object> { new { runId = run.RunId, role = "user", text = user.ValueKind == JsonValueKind.Undefined ? "" : UserText(user),
                attachments = user.ValueKind == JsonValueKind.Undefined ? [] : AttachmentNames(user) } };
            if (run.Output != null) entries.Add(new { runId = run.RunId, role = "assistant", text = run.Output, attachments = Array.Empty<string>() });
            return entries;
        }).ToArray();
        var events = await db.ChatEvents.AsNoTracking().Where(e => e.SessionId == id).OrderBy(e => e.CreatedAt).ToListAsync(token);
        return new { session.Id, messages, events = events.Select(e => new { e.Id, e.RunId, e.Kind, e.CreatedAt,
            payload = JsonSerializer.Deserialize<JsonElement>(e.PayloadJson) }), proposals = await basket.ListAsync(id, token),
            runs = session.Runs.Select(r => new { r.RunId, r.Status, r.ErrorCode, r.ErrorMessage, r.StartedAt, r.CompletedAt }),
            basket = await basket.GetAsync(token) };
    }

    private static string UserText(JsonElement item)
    {
        var content = item.GetProperty("content");
        return content.ValueKind == JsonValueKind.String ? content.GetString() ?? "" :
            content.EnumerateArray().First(p => p.GetProperty("type").GetString() == "input_text").GetProperty("text").GetString() ?? "";
    }

    private static string[] AttachmentNames(JsonElement item) => item.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.Array
        ? content.EnumerateArray().Where(p => p.TryGetProperty("type", out var t) && t.GetString() == "local_attachment").Select(p => p.GetProperty("name").GetString()!).ToArray() : [];
}
