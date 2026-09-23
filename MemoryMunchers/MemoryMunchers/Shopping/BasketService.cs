using MemoryMunchers.Agents;
using MemoryMunchers.Persistence;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

namespace MemoryMunchers.Shopping;

public sealed class BasketService(MemoryMunchersDbContext db, ShopperContext shopper,
    ProductLookup products, IOptions<ShoppingOptions> options, TimeProvider clock)
{
    public async Task<BasketView> GetAsync(CancellationToken token)
    {
        var basket = await db.Baskets.AsNoTracking().Include(b => b.Items).SingleOrDefaultAsync(b => b.Id == shopper.Id, token);
        var cards = basket?.Items.Count > 0 ? await products.DetailsAsync(basket.Items.Select(i => i.ProductId), token) : [];
        var lines = cards.Select(p => new BasketLine(p, basket!.Items.Single(i => i.ProductId == p.Id).Quantity,
            p.Price * basket.Items.Single(i => i.ProductId == p.Id).Quantity)).ToArray();
        return new(basket?.Version ?? 0, lines, lines.Where(l => l.Total.HasValue).GroupBy(l => l.Product.Currency)
            .ToDictionary(g => g.Key, g => g.Sum(l => l.Total!.Value)), lines.Any(l => !l.Total.HasValue),
            options.Value.RequireVerifiedStock, ShopJson.SafeUrl(options.Value.CheckoutUrl), ShopJson.SafeUrl(options.Value.BasketUrl));
    }

    public async Task<ProposalView> PrepareAsync(Guid sessionId, Guid runId, string callId,
        IReadOnlyList<RequestedItem> items, CancellationToken token)
    {
        if (!await db.AgentSessions.AnyAsync(s => s.Id == sessionId && s.ShopperId == shopper.Id && s.AgentId == ProductConsultant.Id, token))
            throw new AgentException("session_not_found", "Диалог не найден.", 404);
        if (items.Count is < 1 or > 20 || items.Select(i => i.ProductId).Distinct().Count() != items.Count)
            throw new AgentToolInputException("Укажите от 1 до 20 разных товаров.");
        var previous = await db.BasketProposals.AsNoTracking().SingleOrDefaultAsync(p => p.RunId == runId && p.CallId == callId && p.ShopperId == shopper.Id, token);
        if (previous != null) return View(previous);
        await using var transaction = await db.Database.BeginTransactionAsync(token);
        var basket = await LockAsync(token);
        var cards = await products.DetailsAsync(items.Select(i => i.ProductId), token);
        var lines = items.Select(i =>
        {
            var product = cards.Single(p => p.Id == i.ProductId);
            if (i.Quantity <= 0) throw new AgentToolInputException("Количество для добавления должно быть положительным.");
            var resulting = i.Quantity + (basket.Items.SingleOrDefault(b => b.ProductId == i.ProductId)?.Quantity ?? 0);
            ValidateQuantity(product, resulting, options.Value.RequireVerifiedStock);
            return new ProposalLine(product, i.Quantity, resulting);
        }).ToArray();
        if (basket.Items.Select(i => i.ProductId).Union(items.Select(i => i.ProductId)).Count() > 20)
            throw new AgentException("basket_limit", "В корзине может быть до 20 разных товаров.", 422);
        await db.BasketProposals.Where(p => p.ShopperId == shopper.Id && p.SessionId == sessionId && p.Status == "pending")
            .ExecuteUpdateAsync(s => s.SetProperty(p => p.Status, "superseded"), token);
        var proposal = new BasketProposal { ShopperId = shopper.Id, SessionId = sessionId, RunId = runId,
            CallId = callId, BasketVersion = basket.Version, LinesJson = ShopJson.Write(lines), CreatedAt = clock.GetUtcNow(),
            ExpiresAt = clock.GetUtcNow().AddMinutes(options.Value.ProposalLifetimeMinutes) };
        db.BasketProposals.Add(proposal);
        await db.SaveChangesAsync(token);
        await transaction.CommitAsync(token);
        return View(proposal);
    }

    public async Task<BasketView> ConfirmAsync(Guid id, CancellationToken token)
    {
        await using var transaction = await db.Database.BeginTransactionAsync(token);
        var basket = await LockAsync(token);
        var proposal = await FindAsync(id, token);
        if (proposal.Status == "confirmed") return await GetAsync(token);
        if (proposal.Status != "pending") throw new AgentException("proposal_inactive", "Предложение уже закрыто. Запросите новое.", 409);
        if (proposal.ExpiresAt <= clock.GetUtcNow())
        {
            await InvalidateAsync("expired");
            throw new AgentException("proposal_expired", "Время подтверждения истекло. Обновите предложение.", 409);
        }
        var lines = ShopJson.Read<ProposalLine[]>(proposal.LinesJson);
        var current = await products.DetailsAsync(lines.Select(l => l.Product.Id), token);
        var changed = basket.Version != proposal.BasketVersion;
        foreach (var line in lines)
        {
            var product = current.Single(p => p.Id == line.Product.Id);
            changed |= product.Price != line.Product.Price || product.Currency != line.Product.Currency ||
                product.Code != line.Product.Code || product.Name != line.Product.Name ||
                product.Availability != line.Product.Availability || product.Unit != line.Product.Unit ||
                product.AvailableQuantity.HasValue != line.Product.AvailableQuantity.HasValue ||
                product.MinimumOrder != line.Product.MinimumOrder || product.OrderMultiple != line.Product.OrderMultiple ||
                product.WebsiteOrderLimit != line.Product.WebsiteOrderLimit;
            try { ValidateQuantity(product, line.ResultingQuantity, options.Value.RequireVerifiedStock); }
            catch (AgentToolInputException) { changed = true; }
        }
        if (changed)
        {
            await InvalidateAsync("stale");
            throw new AgentException("proposal_changed", "Цена, количество или доступность изменились. Обновите предложение и подтвердите его заново.", 409);
        }
        foreach (var line in lines)
        {
            var affected = await db.Set<BasketItem>().Where(i => i.BasketId == shopper.Id && i.ProductId == line.Product.Id)
                .ExecuteUpdateAsync(s => s.SetProperty(i => i.Quantity, line.ResultingQuantity), token);
            if (affected == 0) db.Set<BasketItem>().Add(new BasketItem { BasketId = shopper.Id, ProductId = line.Product.Id, Quantity = line.ResultingQuantity });
        }
        await db.Baskets.Where(b => b.Id == shopper.Id).ExecuteUpdateAsync(s => s.SetProperty(b => b.Version, b => b.Version + 1), token);
        proposal.Status = "confirmed";
        proposal.ConfirmedAt = clock.GetUtcNow();
        db.ChatEvents.Add(new ChatEvent { SessionId = proposal.SessionId, Kind = "basket_confirmed",
            CreatedAt = clock.GetUtcNow(), PayloadJson = ShopJson.Write(new { proposalId = proposal.Id,
                message = "Товары добавлены в корзину после подтверждения.", lines,
                basketUrl = ShopJson.SafeUrl(options.Value.BasketUrl), checkoutUrl = ShopJson.SafeUrl(options.Value.CheckoutUrl) }) });
        await db.SaveChangesAsync(token);
        await transaction.CommitAsync(token);
        return await GetAsync(token);

        async Task InvalidateAsync(string status)
        {
            proposal.Status = status;
            await db.SaveChangesAsync(token);
            await transaction.CommitAsync(token);
        }
    }

    public async Task<ProposalView> CancelAsync(Guid id, CancellationToken token)
    {
        await using var transaction = await db.Database.BeginTransactionAsync(token);
        await LockAsync(token);
        var proposal = await FindAsync(id, token);
        if (proposal.Status == "pending") proposal.Status = "cancelled";
        await db.SaveChangesAsync(token);
        await transaction.CommitAsync(token);
        return View(proposal);
    }

    public async Task<ProposalView> RefreshAsync(Guid id, CancellationToken token)
    {
        var proposal = await FindAsync(id, token);
        if (proposal.Status == "confirmed") throw new AgentException("proposal_inactive", "Товары уже добавлены.", 409);
        var items = ShopJson.Read<ProposalLine[]>(proposal.LinesJson).Select(l => new RequestedItem(l.Product.Id, l.Quantity)).ToArray();
        return await PrepareAsync(proposal.SessionId, proposal.RunId, "refresh-" + Guid.NewGuid(), items, token);
    }

    public async Task<BasketView> SetQuantityAsync(int productId, decimal quantity, int version, CancellationToken token)
    {
        await using var transaction = await db.Database.BeginTransactionAsync(token);
        var basket = await LockAsync(token);
        if (basket.Version != version) throw new AgentException("basket_changed", "Корзина изменилась. Обновите страницу.", 409);
        if (!basket.Items.Any(i => i.ProductId == productId)) throw new AgentException("item_not_found", "Товар не найден в корзине.", 404);
        if (quantity != 0) ValidateQuantity((await products.DetailsAsync([productId], token))[0], quantity, options.Value.RequireVerifiedStock);
        var query = db.Set<BasketItem>().Where(i => i.BasketId == shopper.Id && i.ProductId == productId);
        if (quantity == 0) await query.ExecuteDeleteAsync(token);
        else await query.ExecuteUpdateAsync(s => s.SetProperty(i => i.Quantity, quantity), token);
        await db.Baskets.Where(b => b.Id == shopper.Id).ExecuteUpdateAsync(s => s.SetProperty(b => b.Version, b => b.Version + 1), token);
        await transaction.CommitAsync(token);
        return await GetAsync(token);
    }

    public async Task<IReadOnlyList<ProposalView>> ListAsync(Guid sessionId, CancellationToken token) =>
        (await db.BasketProposals.AsNoTracking().Where(p => p.ShopperId == shopper.Id && p.SessionId == sessionId)
            .OrderBy(p => p.CreatedAt).ToListAsync(token)).Select(View).ToArray();

    public static void ValidateQuantity(ProductCard product, decimal quantity, bool requireVerifiedStock)
    {
        if (quantity <= 0 || quantity > 1_000_000 || decimal.Round(quantity, 3) != quantity)
            throw new AgentToolInputException("Количество должно быть положительным, до 1 000 000, не более трёх знаков после запятой.");
        if (product.MinimumOrder > 0 && quantity < product.MinimumOrder)
            throw new AgentToolInputException($"Минимальное количество для {product.Code}: {product.MinimumOrder}.");
        if (product.OrderMultiple > 0 && quantity % product.OrderMultiple != 0)
            throw new AgentToolInputException($"Количество для {product.Code} должно быть кратно {product.OrderMultiple}.");
        if (product.WebsiteOrderLimit.HasValue && quantity > product.WebsiteOrderLimit)
            throw new AgentToolInputException($"Превышен лимит заказа для {product.Code}: {product.WebsiteOrderLimit}.");
        if (product.AvailableQuantity.HasValue && quantity > product.AvailableQuantity)
            throw new AgentToolInputException($"Недостаточно товара {product.Code}. Доступно: {product.AvailableQuantity}.");
        if (requireVerifiedStock && (!product.AvailableQuantity.HasValue || string.IsNullOrWhiteSpace(product.Unit)))
            throw new AgentToolInputException("Для покупки необходимы актуальные остатки и подтверждённая единица продажи.");
        if (!product.AvailableQuantity.HasValue && product.Availability is not ("Купить" or "Под заказ"))
            throw new AgentToolInputException("Доступность товара не подтверждена.");
    }

    private async Task<Basket> LockAsync(CancellationToken token)
    {
        await db.Database.ExecuteSqlInterpolatedAsync($"INSERT INTO \"Baskets\" (\"Id\", \"Version\") VALUES ({shopper.Id}, 0) ON CONFLICT (\"Id\") DO NOTHING", token);
        return await db.Baskets.FromSqlInterpolated($"SELECT * FROM \"Baskets\" WHERE \"Id\" = {shopper.Id} FOR UPDATE")
            .AsNoTracking().Include(b => b.Items).SingleAsync(token);
    }
    private async Task<BasketProposal> FindAsync(Guid id, CancellationToken token) =>
        await db.BasketProposals.SingleOrDefaultAsync(p => p.Id == id && p.ShopperId == shopper.Id, token)
        ?? throw new AgentException("proposal_not_found", "Предложение не найдено.", 404);
    private ProposalView View(BasketProposal p) => new(p.Id, p.SessionId,
        p.Status == "pending" && p.ExpiresAt <= clock.GetUtcNow() ? "expired" : p.Status, p.ExpiresAt, ShopJson.Read<ProposalLine[]>(p.LinesJson),
        ShopJson.SafeUrl(options.Value.BasketUrl));
}
