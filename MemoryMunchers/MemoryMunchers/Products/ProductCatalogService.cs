using MemoryMunchers.Persistence;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Products;

public sealed class ProductCatalogService(MemoryMunchersDbContext db, ProductCatalogReader reader)
{
    public async Task<ProductCatalogStatus> GetStatusAsync(CancellationToken cancellationToken)
    {
        var products = reader.Read(cancellationToken);
        var existingIds = await db.Products.Select(product => product.Id).ToHashSetAsync(cancellationToken);
        return GetStatus(products, existingIds);
    }

    public async Task<ProductCatalogStatus> ImportAsync(CancellationToken cancellationToken)
    {
        // Validate the entire file before writing anything. Each request owns its parsed entities.
        var products = reader.Read(cancellationToken);
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        // Serialize imports across API instances as well as concurrent browser tabs.
        await db.Database.ExecuteSqlRawAsync("SELECT pg_advisory_xact_lock(728194630125)", cancellationToken);
        var existingIds = await db.Products.Select(product => product.Id).ToHashSetAsync(cancellationToken);
        var missing = products.Where(product => !existingIds.Contains(product.Id)).ToArray();

        foreach (var batch in missing.Chunk(250))
        {
            db.Products.AddRange(batch);
            await db.SaveChangesAsync(cancellationToken);
            foreach (var product in batch) db.Entry(product).State = EntityState.Detached;
        }

        await transaction.CommitAsync(cancellationToken);
        existingIds.UnionWith(missing.Select(product => product.Id));
        return GetStatus(products, existingIds) with { ImportedCount = missing.Length };
    }

    private static ProductCatalogStatus GetStatus(IReadOnlyList<Product> products, HashSet<int> existingIds)
    {
        var matchedCount = products.Count(product => existingIds.Contains(product.Id));
        return new ProductCatalogStatus(products.Count, existingIds.Count,
            products.Count - matchedCount, existingIds.Count - matchedCount);
    }
}

public sealed record ProductCatalogStatus(int ExpectedCount, int DatabaseCount, int MissingCount,
    int UnexpectedCount, int ImportedCount = 0)
{
    public bool IsComplete => MissingCount == 0 && UnexpectedCount == 0;
    public bool RequiresImport => MissingCount > 0;
}

public sealed record ProductSummary(int Id, string Code, string SupplierArticle, string Name, string Brand,
    string Category, string CategoryPath, decimal? WebsitePrice, decimal? StorePrice, string Currency,
    string Availability, string Unit, string ProductUrl);

public sealed record ProductPage(IReadOnlyList<ProductSummary> Items, int Page, int PageSize, int TotalCount, int TotalPages);
