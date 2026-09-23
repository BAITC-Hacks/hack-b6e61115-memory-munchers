using System.Net.Http.Json;
using System.Text.Json;
using MemoryMunchers.Agents;
using MemoryMunchers.Persistence;
using MemoryMunchers.Products;
using MemoryMunchers.Shopping;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace MemoryMunchers.Tests;

/// <summary>
/// Runs the real API against a dedicated PostgreSQL database (memory_munchers_tests by default,
/// override with MM_TEST_CONNECTION). The model provider is never called: agent tools are executed directly.
/// </summary>
public sealed class ShopFixture : WebApplicationFactory<Program>, IAsyncLifetime
{
    public const string AllowedOrigin = "http://localhost:5500";
    public const string BasketUrl = "http://localhost:5500/basket.html";

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Testing");
        builder.UseSetting("ConnectionStrings:MemoryMunchers", Environment.GetEnvironmentVariable("MM_TEST_CONNECTION")
            ?? "Host=localhost;Port=5432;Database=memory_munchers_tests;Username=postgres;Password=123");
        builder.UseSetting("DemoData:Enabled", "false");
        builder.UseSetting("OpenAI:ApiKey", "");
        builder.UseSetting("Shopping:BasketUrl", BasketUrl);
        builder.UseSetting("Shopping:PublicApiUrl", "http://localhost");
        builder.UseSetting("RateLimits:ChatPerMinute", "1000");
        builder.UseSetting("RateLimits:BasketPerMinute", "1000");
        builder.UseSetting("RateLimits:ShoppersPerMinute", "1000");
    }

    public async Task InitializeAsync()
    {
        using var client = CreateClient();
        (await client.PostAsync("/api/products/import", null)).EnsureSuccessStatusCode();
    }

    Task IAsyncLifetime.DisposeAsync() => Task.CompletedTask;

    public async Task<Shopper> NewShopperAsync()
    {
        var client = CreateClient();
        var token = (await (await client.PostAsync("/api/shopper", null)).Content.ReadFromJsonAsync<JsonElement>()).GetProperty("token").GetString()!;
        client.DefaultRequestHeaders.Add(ShopperTokenService.HeaderName, token);
        return new Shopper(this, client, Services.GetRequiredService<ShopperTokenService>().Read(token)!.Value);
    }

    public async Task WithDbAsync(Func<MemoryMunchersDbContext, Task> action)
    {
        await using var scope = Services.CreateAsyncScope();
        await action(scope.ServiceProvider.GetRequiredService<MemoryMunchersDbContext>());
    }

    public async Task<T> WithDbAsync<T>(Func<MemoryMunchersDbContext, Task<T>> action)
    {
        await using var scope = Services.CreateAsyncScope();
        return await action(scope.ServiceProvider.GetRequiredService<MemoryMunchersDbContext>());
    }

    public Task SetStockAsync(int productId, decimal quantity) => WithDbAsync(db => db.Database.ExecuteSqlInterpolatedAsync($"""
        INSERT INTO "ProductInventory" ("ProductId", "AvailableQuantity", "CheckedAt") VALUES ({productId}, {quantity}, {DateTimeOffset.UtcNow})
        ON CONFLICT ("ProductId") DO UPDATE SET "AvailableQuantity" = EXCLUDED."AvailableQuantity", "CheckedAt" = EXCLUDED."CheckedAt"
        """));
}

public sealed record Shopper(ShopFixture Fixture, HttpClient Client, Guid Id)
{
    public async Task<Guid> NewSessionAsync()
    {
        var response = await Client.PostAsync("/api/product-chat/sessions", null);
        response.EnsureSuccessStatusCode();
        return (await response.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetGuid();
    }

    // Executes an agent tool exactly as the agent runner would, within this shopper's identity.
    public async Task<JsonElement> ToolAsync(string name, object arguments, Guid? sessionId = null)
    {
        await using var scope = Fixture.Services.CreateAsyncScope();
        scope.ServiceProvider.GetRequiredService<ShopperContext>().Id = Id;
        var tool = scope.ServiceProvider.GetServices<IAgentTool>().Single(t => t.Definition.Name == name);
        var json = await tool.ExecuteAsync(JsonSerializer.SerializeToElement(arguments),
            new AgentToolContext(sessionId ?? Guid.Empty, Guid.NewGuid(), "call-" + Guid.NewGuid()), CancellationToken.None);
        return JsonDocument.Parse(json).RootElement.Clone();
    }

    public async Task<JsonElement> BasketAsync() => await Client.GetFromJsonAsync<JsonElement>("/api/basket");
}

[CollectionDefinition(Name)]
public sealed class ShopCollection : ICollectionFixture<ShopFixture>
{
    public const string Name = "shop";
}
