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
        var cutoff = clock.GetUtcNow().AddMinutes(-options.Value.InventoryMaxAgeMinutes);
        var path = await db.Products.Where(p => p.Id == id).Select(p => p.CategoryPath).SingleAsync(token);
        var originalWords = NameWords(original.Name);
        // The first name word usually names the product type ("Автомат", "Кабель"); same-type products enter the pool first.
        var typePattern = Pattern(originalWords.FirstOrDefault() ?? original.Name);
        // Products known to be out of stock are useless as replacements, so they are excluded before ranking.
        var candidates = await db.Products.AsNoTracking().Where(p => p.Id != id && p.Category == original.Category)
            .Select(p => new { Product = p, Stock = db.ProductInventory.Where(i => i.ProductId == p.Id && i.CheckedAt >= cutoff)
                .Select(i => (decimal?)i.AvailableQuantity).FirstOrDefault() })
            .Where(x => x.Stock == null || x.Stock > 0)
            .OrderByDescending(x => EF.Functions.ILike(x.Product.Name, typePattern)).ThenByDescending(x => x.Product.CategoryPath == path)
            .ThenByDescending(x => x.Stock != null).ThenByDescending(x => x.Product.Availability == "Купить").ThenBy(x => x.Product.Id)
            .Take(300).ToListAsync(token);
        var originalSpecs = original.Specifications.EnumerateObject()
            .Where(s => !s.Name.Contains("Артикул", StringComparison.OrdinalIgnoreCase) && !s.Name.StartsWith("Кол-во", StringComparison.OrdinalIgnoreCase)
                && s.Name is not ("Новинка" or "Торговая марка"))
            .ToDictionary(p => p.Name, p => p.Value.ToString());
        var ranked = candidates.Select(c =>
        {
            using var specs = JsonDocument.Parse(c.Product.SpecificationsJson);
            var values = specs.RootElement.EnumerateObject().Where(s => originalSpecs.ContainsKey(s.Name))
                .ToDictionary(s => s.Name, s => s.Value.ToString());
            var matches = values.Where(v => originalSpecs[v.Key] == v.Value).Select(v => v.Key).ToArray();
            var differences = values.Where(v => originalSpecs[v.Key] != v.Value)
                .Select(v => new AttributeDifference(v.Key, originalSpecs[v.Key], v.Value)).ToArray();
            var sameBrand = c.Product.Brand == original.Brand && original.Brand.Length > 0;
            var priceDelta = original.Price > 0 && c.Product.WebsitePrice.HasValue
                ? (c.Product.WebsitePrice.Value - original.Price.Value) / original.Price.Value : (decimal?)null;
            var availabilityRank = c.Stock > 0 ? 0 : c.Product.Availability == "Купить" ? 1 : 2;
            // Attribute coverage dominates; stock and price only break ties between equally similar products.
            var score = originalSpecs.Count == 0 ? 0 : (double)matches.Length / originalSpecs.Count;
            var sameSubcategory = c.Product.CategoryPath == path;
            var words = NameWords(c.Product.Name);
            var nameOverlap = originalWords.Count == 0 ? 0 : (double)words.Count(originalWords.Contains) / originalWords.Count;
            // Product type (name words) and shared attributes dominate; subcategory, brand and stock refine the order.
            var rank = 4 * nameOverlap + matches.Length - 0.5 * differences.Length + (sameSubcategory ? 1.5 : 0) + (sameBrand ? 0.5 : 0);
            return new { c.Product, SameSubcategory = sameSubcategory, Matches = matches, Differences = differences, SameBrand = sameBrand,
                PriceDelta = priceDelta, AvailabilityRank = availabilityRank, NameOverlap = nameOverlap, Rank = rank,
                Score = Math.Min(1, 0.5 * nameOverlap + 0.5 * score) };
        }).OrderByDescending(x => x.Rank).ThenBy(x => x.AvailabilityRank)
            .ThenByDescending(x => x.SameBrand).ThenBy(x => x.PriceDelta.HasValue ? Math.Abs(x.PriceDelta.Value) : decimal.MaxValue)
            .Take(5).ToArray();
        var cards = await CardsAsync(ranked.Select(x => x.Product).ToArray(), false, token);
        return new
        {
            original,
            originalStockStatus = original.StockStatus,
            candidates = ranked.Select((r, i) => new
            {
                product = cards[i], relevance = Math.Round(r.Score, 2), matchingAttributes = r.Matches,
                differingAttributes = r.Differences, sameBrand = r.SameBrand,
                priceDifferencePercent = r.PriceDelta.HasValue ? Math.Round(r.PriceDelta.Value * 100, 1) : (decimal?)null,
                sameSubcategory = r.SameSubcategory,
                reason = Reason(original, cards[i], r.SameSubcategory ? path : null, r.NameOverlap, r.Matches, r.Differences, r.SameBrand, r.PriceDelta),
                compatibilityVerified = false
            }),
            note = "Аналоги из той же категории, известные отсутствующие товары исключены. Ранжирование: тип товара по названию, совпавшие характеристики, подкатегория и бренд; при равенстве — наличие и близость цены. Совместимость не гарантирована."
        };
    }

    // Human-readable explanation of why a candidate was proposed, shown to the shopper next to the product card.
    private static string Reason(ProductCard original, ProductCard candidate, string? sharedPath, double nameOverlap, string[] matches,
        AttributeDifference[] differences, bool sameBrand, decimal? priceDelta)
    {
        var parts = new List<string> { sharedPath == null ? $"Та же категория «{original.Category}», но другая подкатегория"
            : $"Та же подкатегория «{sharedPath.Split('>').Last().Trim()}»" };
        if (nameOverlap >= 0.5) parts.Add("тот же тип товара по наименованию");
        var specs = original.Specifications;
        if (matches.Length > 0)
            parts.Add("совпадают: " + string.Join(", ", matches.Take(5).Select(m => $"{m} {specs.GetProperty(m)}")) +
                (matches.Length > 5 ? $" и ещё {matches.Length - 5}" : ""));
        else parts.Add("общих характеристик не найдено — сверьте параметры вручную");
        if (differences.Length > 0)
            parts.Add("отличаются: " + string.Join(", ", differences.Take(3).Select(d => $"{d.Name} {d.Original} → {d.Candidate}")));
        if (sameBrand) parts.Add($"тот же бренд {original.Brand}");
        if (priceDelta.HasValue)
            parts.Add(priceDelta.Value == 0 ? "та же цена" : $"цена {(priceDelta.Value > 0 ? "+" : "")}{Math.Round(priceDelta.Value * 100)}%");
        parts.Add(candidate.StockStatus switch
        {
            "in_stock" => $"в наличии {candidate.AvailableQuantity:0.###} {candidate.Unit}".TrimEnd(),
            "unknown_orderable" => "доступен к покупке, точный остаток не подтверждён",
            _ => "под заказ"
        });
        return string.Join("; ", parts) + ".";
    }

    private async Task<IReadOnlyList<ProductCard>> CardsAsync(IReadOnlyList<Product> products, bool description, CancellationToken token)
    {
        var ids = products.Select(p => p.Id).ToArray();
        var inventory = await db.ProductInventory.AsNoTracking().Where(i => ids.Contains(i.ProductId)).ToDictionaryAsync(i => i.ProductId, token);
        var certificates = (await db.ProductCertificates.AsNoTracking().Where(c => ids.Contains(c.ProductId)).OrderBy(c => c.Id).ToListAsync(token))
            .ToLookup(c => c.ProductId);
        var apiUrl = ShopJson.SafeUrl(options.Value.PublicApiUrl)?.TrimEnd('/');
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
                description ? p.Description[..Math.Min(p.Description.Length, 6000)] : null,
                certificates[p.Id].Take(10).Select(c => new CertificateView(c.Id, c.Number, c.Type, c.IssuedBy, c.ValidUntil,
                    ShopJson.SafeUrl(c.Url) ?? (apiUrl == null ? null : $"{apiUrl}/api/certificates/{c.Id}"))).ToArray());
        }).ToArray();
    }

    // Lower-cased alphabetic words of 4+ letters: "Автомат светочувствительный AZ-112" -> {автомат, светочувствительный}.
    private static List<string> NameWords(string name) => System.Text.RegularExpressions.Regex.Matches(name.ToLowerInvariant(), @"\p{L}{4,}")
        .Select(m => m.Value).Distinct().ToList();

    private sealed record AttributeDifference(string Name, string Original, string Candidate);

    private static string Pattern(string value) => "%" + value.Trim().Replace("\\", "\\\\").Replace("%", "\\%").Replace("_", "\\_") + "%";
}
