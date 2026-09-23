using System.Text.Json;
using MemoryMunchers.Agents;
using MemoryMunchers.Persistence;
using MemoryMunchers.Products;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

namespace MemoryMunchers.Shopping;

public sealed class ProductLookup(MemoryMunchersDbContext db, IOptions<ShoppingOptions> options, TimeProvider clock)
{
    public async Task<IReadOnlyList<ProductCard>> SearchAsync(string query, string? category, string? brand,
        decimal? maxPrice, CancellationToken token)
    {
        if (query.Length > 200 || category?.Length > 200 || brand?.Length > 100 || maxPrice < 0)
            throw new AgentToolInputException("Поисковый запрос слишком длинный или цена некорректна.");
        var products = db.Products.AsNoTracking().AsQueryable();
        if (!string.IsNullOrWhiteSpace(category)) products = products.Where(p => EF.Functions.ILike(p.CategoryPath, Pattern(category)));
        if (!string.IsNullOrWhiteSpace(brand)) products = products.Where(p => EF.Functions.ILike(p.Brand, Pattern(brand)));
        if (maxPrice.HasValue) products = products.Where(p => p.WebsitePrice <= maxPrice);
        var exact = query.Trim();
        if (exact.Length > 0)
        {
            var pattern = Pattern(exact);
            products = products.Where(p => p.Code == exact || p.SupplierArticle == exact ||
                EF.Functions.ILike(p.SearchText, pattern));
        }
        var found = await products.OrderByDescending(p => p.Code == exact || p.SupplierArticle == exact)
            .ThenByDescending(p => p.Availability == "Купить").ThenBy(p => p.Id).Take(8).ToListAsync(token);
        // Multi-word requests often differ in word order from catalog names.
        if (found.Count == 0 && exact.Contains(' '))
        {
            products = db.Products.AsNoTracking();
            foreach (var word in exact.Split(' ', StringSplitOptions.RemoveEmptyEntries).Take(6))
            {
                var pattern = Pattern(word);
                products = products.Where(p => EF.Functions.ILike(p.SearchText, pattern));
            }
            if (!string.IsNullOrWhiteSpace(category)) products = products.Where(p => EF.Functions.ILike(p.CategoryPath, Pattern(category)));
            if (!string.IsNullOrWhiteSpace(brand)) products = products.Where(p => EF.Functions.ILike(p.Brand, Pattern(brand)));
            if (maxPrice.HasValue) products = products.Where(p => p.WebsitePrice <= maxPrice);
            found = await products.OrderBy(p => p.Id).Take(8).ToListAsync(token);
        }
        return await CardsAsync(found, false, token);
    }

    public async Task<IReadOnlyList<ProductCard>> DetailsAsync(IEnumerable<int> ids, CancellationToken token)
    {
        var keys = ids.Distinct().ToArray();
        if (keys.Length is < 1 or > 20 || keys.Any(id => id <= 0)) throw new AgentToolInputException("Укажите от 1 до 20 ID товаров.");
        var products = await db.Products.AsNoTracking().Where(p => keys.Contains(p.Id)).ToListAsync(token);
        if (products.Count != keys.Length) throw new AgentException("product_not_found", "Один из товаров больше не доступен в каталоге.", 404);
        return await CardsAsync(products.OrderBy(p => Array.IndexOf(keys, p.Id)).ToArray(), true, token);
    }

    public async Task<object> AlternativesAsync(int id, CancellationToken token)
    {
        var original = (await DetailsAsync([id], token))[0];
        var candidates = await db.Products.AsNoTracking().Where(p => p.Id != id && p.Category == original.Category)
            .OrderByDescending(p => p.Availability == "Купить").ThenBy(p => p.Id).Take(100).ToListAsync(token);
        var originalSpecs = original.Specifications.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.ToString());
        var ranked = candidates.Select(p =>
        {
            using var specs = JsonDocument.Parse(p.SpecificationsJson);
            var comparable = specs.RootElement.EnumerateObject().Where(s => originalSpecs.ContainsKey(s.Name)
                && !s.Name.Contains("Артикул", StringComparison.OrdinalIgnoreCase) && s.Name != "Новинка").ToArray();
            var matches = comparable.Where(s => originalSpecs[s.Name] == s.Value.ToString()).Select(s => s.Name).ToArray();
            var differences = comparable.Where(s => originalSpecs[s.Name] != s.Value.ToString()).Select(s => s.Name).ToArray();
            return new { Product = p, Matches = matches, Differences = differences };
        }).OrderByDescending(x => x.Matches.Length).ThenBy(x => x.Differences.Length).Take(5).ToArray();
        var cards = await CardsAsync(ranked.Select(x => x.Product).ToArray(), false, token);
        return new { original, candidates = ranked.Select((r, i) => new { product = cards[i], matchingAttributes = r.Matches,
            differingAttributes = r.Differences, compatibilityVerified = false }),
            note = "Кандидаты из той же категории. Уточните критические характеристики; совпадение категории не гарантирует совместимость." };
    }

    private async Task<IReadOnlyList<ProductCard>> CardsAsync(IReadOnlyList<Product> products, bool description, CancellationToken token)
    {
        var ids = products.Select(p => p.Id).ToArray();
        var inventory = await db.ProductInventory.AsNoTracking().Where(i => ids.Contains(i.ProductId)).ToDictionaryAsync(i => i.ProductId, token);
        var cutoff = clock.GetUtcNow().AddMinutes(-options.Value.InventoryMaxAgeMinutes);
        return products.Select(p =>
        {
            inventory.TryGetValue(p.Id, out var stock);
            using var specs = JsonDocument.Parse(p.SpecificationsJson);
            var documents = p.DocumentUrls.Split(['\n', '\r', '|', ';', ' '], StringSplitOptions.RemoveEmptyEntries)
                .Select(ShopJson.SafeUrl).Where(u => u != null).Cast<string>().Distinct().Take(10).ToArray();
            return new ProductCard(p.Id, p.Code, p.Name, p.Brand, p.Category, p.WebsitePrice, p.Currency,
                p.Availability, p.Unit, p.MinimumOrder, p.OrderMultiple, p.WebsiteOrderLimit,
                stock?.CheckedAt >= cutoff ? stock.AvailableQuantity : null, stock?.CheckedAt,
                p.RetrievedAtUtc, ShopJson.SafeUrl(p.ProductUrl), documents, specs.RootElement.Clone(),
                description ? p.Description[..Math.Min(p.Description.Length, 6000)] : null);
        }).ToArray();
    }

    private static string Pattern(string value) => "%" + value.Trim().Replace("\\", "\\\\").Replace("%", "\\%").Replace("_", "\\_") + "%";
}
