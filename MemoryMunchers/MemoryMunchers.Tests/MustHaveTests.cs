using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using MemoryMunchers.Agents;
using MemoryMunchers.Products;
using MemoryMunchers.Shopping;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Tests;

// One test per acceptance check of the "Must have" requirements, plus the cross-cutting security and privacy rules.
[Collection(ShopCollection.Name)]
public sealed class MustHaveTests(ShopFixture fixture)
{
    // Products the tests mutate stock for; each test picks its own so they stay independent.
    private Task<Product> ProductAsync(int skip) => fixture.WithDbAsync(db => db.Products.AsNoTracking()
        .Where(p => p.SpecificationsJson != "{}" && p.Code != "" && p.Availability == "Купить" && (p.OrderMultiple == null || p.OrderMultiple == 1)
            && (p.MinimumOrder == null || p.MinimumOrder <= 1) && (p.WebsiteOrderLimit == null || p.WebsiteOrderLimit >= 10)
            && db.Products.Count(o => o.Category == p.Category) >= 5)
        .OrderBy(p => p.Id).Skip(skip).FirstAsync());

    [Fact]
    public async Task R1_ProductQuestion_ReturnsStockSpecificationsAndCertificate()
    {
        var product = await ProductAsync(0);
        await fixture.SetStockAsync(product.Id, 7);
        var certificate = await fixture.WithDbAsync(async db =>
        {
            await db.ProductCertificates.Where(c => c.ProductId == product.Id).ExecuteDeleteAsync();
            var entity = new ProductCertificate { ProductId = product.Id, Number = "TEST-" + product.Id, Type = "Сертификат соответствия", IssuedBy = "Тест",
                ValidUntil = new DateOnly(2030, 1, 1) };
            db.ProductCertificates.Add(entity);
            await db.SaveChangesAsync();
            return entity;
        });
        var shopper = await fixture.NewShopperAsync();

        var found = await shopper.ToolAsync("search_products", new { query = product.Code, category = (string?)null, brand = (string?)null, maxPrice = (decimal?)null });
        Assert.Equal(product.Id, found[0].GetProperty("id").GetInt32());

        var details = (await shopper.ToolAsync("get_product_details", new { productIds = new[] { product.Id } }))[0];
        Assert.Equal(7m, details.GetProperty("availableQuantity").GetDecimal());
        Assert.Equal("in_stock", details.GetProperty("stockStatus").GetString());
        using var specs = JsonDocument.Parse(product.SpecificationsJson);
        Assert.Equal(specs.RootElement.EnumerateObject().Count(), details.GetProperty("specifications").EnumerateObject().Count());
        var link = details.GetProperty("certificates")[0];
        Assert.Equal(certificate.Number, link.GetProperty("number").GetString());
        var url = new Uri(link.GetProperty("url").GetString()!);
        Assert.Equal($"/api/certificates/{certificate.Id}", url.AbsolutePath);

        var page = await shopper.Client.GetStringAsync(url.AbsolutePath);
        Assert.Contains(certificate.Number, page);
    }

    [Fact]
    public async Task R2_OutOfStockProduct_OffersAlternativeWithReason()
    {
        var product = await ProductAsync(1);
        await fixture.SetStockAsync(product.Id, 0);
        await fixture.WithDbAsync(db => db.Database.ExecuteSqlInterpolatedAsync($"""
            INSERT INTO "ProductInventory" ("ProductId", "AvailableQuantity", "CheckedAt")
            SELECT "Id", 10, {DateTimeOffset.UtcNow} FROM "Products" WHERE "Category" = {product.Category} AND "Id" <> {product.Id}
            ON CONFLICT ("ProductId") DO UPDATE SET "AvailableQuantity" = 10, "CheckedAt" = EXCLUDED."CheckedAt"
            """));
        var shopper = await fixture.NewShopperAsync();

        var details = (await shopper.ToolAsync("get_product_details", new { productIds = new[] { product.Id } }))[0];
        Assert.Equal("out_of_stock", details.GetProperty("stockStatus").GetString());

        var result = await shopper.ToolAsync("find_product_alternatives", new { productId = product.Id });
        var candidates = result.GetProperty("candidates").EnumerateArray().ToArray();
        Assert.NotEmpty(candidates);
        Assert.All(candidates, c =>
        {
            Assert.False(string.IsNullOrWhiteSpace(c.GetProperty("reason").GetString()));
            Assert.Contains("категори", c.GetProperty("reason").GetString());
            Assert.NotEqual(product.Id, c.GetProperty("product").GetProperty("id").GetInt32());
            Assert.NotEqual("out_of_stock", c.GetProperty("product").GetProperty("stockStatus").GetString());
            Assert.False(c.GetProperty("compatibilityVerified").GetBoolean());
        });
    }

    [Fact]
    public async Task R3_PurchaseConditionsQuestion_GetsSubstantiveAnswer()
    {
        var product = await ProductAsync(2);
        var shopper = await fixture.NewShopperAsync();
        var result = await shopper.ToolAsync("get_purchase_conditions", new { productIds = new[] { product.Id } });

        Assert.True(result.GetProperty("configured").GetBoolean());
        var conditions = result.GetProperty("conditions");
        Assert.NotEmpty(conditions.GetProperty("payment").EnumerateArray());
        Assert.NotEmpty(conditions.GetProperty("delivery").EnumerateArray());
        Assert.False(string.IsNullOrWhiteSpace(conditions.GetProperty("minimumOrder").GetString()));
        Assert.NotNull(result.GetProperty("sourceUrl").GetString());
        var line = result.GetProperty("products")[0];
        Assert.Equal(product.Id, line.GetProperty("id").GetInt32());
        Assert.True(line.TryGetProperty("minimumOrder", out _) && line.TryGetProperty("orderMultiple", out _));
    }

    [Fact]
    public async Task R4_BasketChangesOnlyAfterExplicitConfirmation_AndNeverExceedsStock()
    {
        var product = await ProductAsync(3);
        await fixture.SetStockAsync(product.Id, 5);
        var shopper = await fixture.NewShopperAsync();
        var session = await shopper.NewSessionAsync();
        object Items(decimal quantity) => new { items = new[] { new { productId = product.Id, quantity } } };

        // The agent may only prepare a proposal; the basket stays unchanged until the shopper confirms.
        var proposal = await shopper.ToolAsync("prepare_basket_addition", Items(2), session);
        Assert.Equal("pending", proposal.GetProperty("status").GetString());
        Assert.Empty((await shopper.BasketAsync()).GetProperty("items").EnumerateArray());

        // More than the stock cannot even be proposed.
        await Assert.ThrowsAsync<AgentToolInputException>(() => shopper.ToolAsync("prepare_basket_addition", Items(6), session));
        Assert.Empty((await shopper.BasketAsync()).GetProperty("items").EnumerateArray());

        // A rejected proposal leaves the earlier pending one intact; the shopper's explicit confirmation applies it.
        var confirmed = await shopper.Client.PostAsync($"/api/basket/proposals/{proposal.GetProperty("id").GetGuid()}/confirm", null);
        Assert.Equal(HttpStatusCode.OK, confirmed.StatusCode);
        var basket = await shopper.BasketAsync();
        Assert.Equal(2m, basket.GetProperty("items")[0].GetProperty("quantity").GetDecimal());

        // 2 in the basket + 4 would exceed the stock of 5.
        await Assert.ThrowsAsync<AgentToolInputException>(() => shopper.ToolAsync("prepare_basket_addition", Items(4), session));

        // Stock drops between the proposal and the confirmation: the confirmation is rejected, basket unchanged.
        var second = await shopper.ToolAsync("prepare_basket_addition", Items(3), session);
        await fixture.SetStockAsync(product.Id, 3);
        var stale = await shopper.Client.PostAsync($"/api/basket/proposals/{second.GetProperty("id").GetGuid()}/confirm", null);
        Assert.Equal(HttpStatusCode.Conflict, stale.StatusCode);
        Assert.Equal(2m, (await shopper.BasketAsync()).GetProperty("items")[0].GetProperty("quantity").GetDecimal());

        // Direct basket edits are validated against stock as well.
        var version = (await shopper.BasketAsync()).GetProperty("version").GetInt32();
        var tooMany = await shopper.Client.PatchAsJsonAsync($"/api/basket/items/{product.Id}", new { quantity = 4, version });
        Assert.Equal(HttpStatusCode.UnprocessableEntity, tooMany.StatusCode);
    }

    [Fact]
    public async Task R5_AfterConfirmation_LinkLeadsToCurrentBasket()
    {
        var product = await ProductAsync(4);
        await fixture.SetStockAsync(product.Id, 10);
        var shopper = await fixture.NewShopperAsync();
        var session = await shopper.NewSessionAsync();
        var proposal = await shopper.ToolAsync("prepare_basket_addition", new { items = new[] { new { productId = product.Id, quantity = 3m } } }, session);
        Assert.Equal(ShopFixture.BasketUrl, proposal.GetProperty("basketUrl").GetString());

        var response = await shopper.Client.PostAsync($"/api/basket/proposals/{proposal.GetProperty("id").GetGuid()}/confirm", null);
        var confirmed = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(ShopFixture.BasketUrl, confirmed.GetProperty("basketUrl").GetString());

        // The basket page reads GET /api/basket with the same shopper token, so it always shows the live state.
        var version = confirmed.GetProperty("version").GetInt32();
        (await shopper.Client.PatchAsJsonAsync($"/api/basket/items/{product.Id}", new { quantity = 1, version })).EnsureSuccessStatusCode();
        var basket = await shopper.BasketAsync();
        Assert.Equal(1m, basket.GetProperty("items")[0].GetProperty("quantity").GetDecimal());

        var chat = await shopper.Client.GetFromJsonAsync<JsonElement>($"/api/product-chat/sessions/{session}");
        var confirmation = chat.GetProperty("events").EnumerateArray().Single(e => e.GetProperty("kind").GetString() == "basket_confirmed");
        Assert.Equal(ShopFixture.BasketUrl, confirmation.GetProperty("payload").GetProperty("basketUrl").GetString());
    }

    [Fact]
    public async Task Security_ShoppersAreIsolated_AndCannotChangeEachOthersBasket()
    {
        var product = await ProductAsync(5);
        await fixture.SetStockAsync(product.Id, 10);
        var alice = await fixture.NewShopperAsync();
        var mallory = await fixture.NewShopperAsync();
        var session = await alice.NewSessionAsync();
        var proposal = await alice.ToolAsync("prepare_basket_addition", new { items = new[] { new { productId = product.Id, quantity = 1m } } }, session);
        var id = proposal.GetProperty("id").GetGuid();

        Assert.Equal(HttpStatusCode.NotFound, (await mallory.Client.PostAsync($"/api/basket/proposals/{id}/confirm", null)).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await mallory.Client.GetAsync($"/api/product-chat/sessions/{session}")).StatusCode);
        Assert.DoesNotContain(session.ToString(), await mallory.Client.GetStringAsync("/api/product-chat/sessions"));
        Assert.Empty((await alice.BasketAsync()).GetProperty("items").EnumerateArray());

        using var anonymous = fixture.CreateClient();
        Assert.Equal(HttpStatusCode.Unauthorized, (await anonymous.GetAsync("/api/basket")).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, (await anonymous.PostAsync($"/api/basket/proposals/{id}/confirm", null)).StatusCode);
        anonymous.DefaultRequestHeaders.Add(ShopperTokenService.HeaderName, alice.Id.ToString("N"));
        Assert.Equal(HttpStatusCode.Unauthorized, (await anonymous.GetAsync("/api/basket")).StatusCode);
    }

    [Theory]
    [InlineData(ShopFixture.AllowedOrigin, true)]
    [InlineData("https://evil.example", false)]
    public async Task Security_OnlyStorefrontOriginPassesCors(string origin, bool allowed)
    {
        using var client = fixture.CreateClient();
        var request = new HttpRequestMessage(HttpMethod.Options, "/api/basket/proposals/00000000-0000-0000-0000-000000000000/confirm");
        request.Headers.Add("Origin", origin);
        request.Headers.Add("Access-Control-Request-Method", "POST");
        request.Headers.Add("Access-Control-Request-Headers", ShopperTokenService.HeaderName);
        var response = await client.SendAsync(request);
        Assert.Equal(allowed, response.Headers.Contains("Access-Control-Allow-Origin"));
    }

    [Fact]
    public async Task Privacy_ShopperCanDeleteAllOwnData()
    {
        var product = await ProductAsync(6);
        await fixture.SetStockAsync(product.Id, 10);
        var shopper = await fixture.NewShopperAsync();
        var session = await shopper.NewSessionAsync();
        var proposal = await shopper.ToolAsync("prepare_basket_addition", new { items = new[] { new { productId = product.Id, quantity = 1m } } }, session);
        (await shopper.Client.PostAsync($"/api/basket/proposals/{proposal.GetProperty("id").GetGuid()}/confirm", null)).EnsureSuccessStatusCode();

        Assert.Equal(HttpStatusCode.NoContent, (await shopper.Client.DeleteAsync("/api/shopper")).StatusCode);
        Assert.Empty((await shopper.BasketAsync()).GetProperty("items").EnumerateArray());
        Assert.Equal(HttpStatusCode.NotFound, (await shopper.Client.GetAsync($"/api/product-chat/sessions/{session}")).StatusCode);
        Assert.False(await fixture.WithDbAsync(db => db.BasketProposals.AnyAsync(p => p.ShopperId == shopper.Id)));
    }
}
