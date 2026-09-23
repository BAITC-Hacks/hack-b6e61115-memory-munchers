using MemoryMunchers.Persistence;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Shopping;

// Anonymous shopper identity resolved per request from a signed token (see ShopperIdentityMiddleware).
public sealed class ShopperContext
{
    public Guid Id { get; set; }
    public bool IsResolved => Id != Guid.Empty;
}

public sealed class ShopperTokenService(IDataProtectionProvider provider)
{
    public const string HeaderName = "X-Shopper-Token";
    private readonly IDataProtector protector = provider.CreateProtector("MemoryMunchers.Shopper.v1");

    public (Guid Id, string Token) Issue()
    {
        var id = Guid.NewGuid();
        return (id, protector.Protect(id.ToString("N")));
    }

    public Guid? Read(string? token)
    {
        if (string.IsNullOrWhiteSpace(token) || token.Length > 1024) return null;
        try { return Guid.TryParseExact(protector.Unprotect(token), "N", out var id) && id != Guid.Empty ? id : null; }
        catch (System.Security.Cryptography.CryptographicException) { return null; }
    }
}

/// <summary>
/// Every basket and chat request must carry a token issued by POST /api/shopper. The token travels in a custom
/// header rather than a cookie, so cross-site pages cannot replay it (no CSRF) and each browser only sees its own data.
/// </summary>
public sealed class ShopperIdentityMiddleware(RequestDelegate next)
{
    private static readonly string[] ProtectedPrefixes = ["/api/basket", "/api/product-chat", "/api/shopper"];

    public async Task InvokeAsync(HttpContext context, ShopperContext shopper, ShopperTokenService tokens)
    {
        var path = context.Request.Path;
        var issuing = HttpMethods.IsPost(context.Request.Method) && path.Equals("/api/shopper", StringComparison.OrdinalIgnoreCase);
        if (HttpMethods.IsOptions(context.Request.Method) || issuing ||
            !ProtectedPrefixes.Any(p => path.StartsWithSegments(p, StringComparison.OrdinalIgnoreCase)))
        {
            await next(context);
            return;
        }
        var id = tokens.Read(context.Request.Headers[ShopperTokenService.HeaderName]);
        if (id == null)
        {
            context.Response.StatusCode = StatusCodes.Status401Unauthorized;
            await context.Response.WriteAsJsonAsync(new { title = "shopper_required", status = 401,
                detail = "Сессия покупателя не найдена. Обновите страницу." });
            return;
        }
        shopper.Id = id.Value;
        await next(context);
    }
}

public sealed class ShopperDataService(MemoryMunchersDbContext db, ShopperContext shopper)
{
    // Right to be forgotten: removes dialogs, uploaded files, proposals and the basket of the current shopper.
    public async Task ForgetAsync(CancellationToken token)
    {
        await using var transaction = await db.Database.BeginTransactionAsync(token);
        var sessions = db.AgentSessions.Where(s => s.ShopperId == shopper.Id).Select(s => s.Id);
        await db.ChatEvents.Where(e => sessions.Contains(e.SessionId)).ExecuteDeleteAsync(token);
        await db.ChatAttachments.Where(a => a.ShopperId == shopper.Id).ExecuteDeleteAsync(token);
        await db.BasketProposals.Where(p => p.ShopperId == shopper.Id).ExecuteDeleteAsync(token);
        await db.AgentSessions.Where(s => s.ShopperId == shopper.Id).ExecuteDeleteAsync(token);
        await db.Baskets.Where(b => b.Id == shopper.Id).ExecuteDeleteAsync(token);
        await transaction.CommitAsync(token);
    }
}
