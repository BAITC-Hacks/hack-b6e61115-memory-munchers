namespace MemoryMunchers.Products;

public sealed class Product
{
    // The source catalog ID is also the primary key, making repeated imports idempotent.
    public int Id { get; set; }
    public string Code { get; set; } = "";
    public string SupplierArticle { get; set; } = "";
    public string Name { get; set; } = "";
    public string Brand { get; set; } = "";
    public string Category { get; set; } = "";
    public string CategoryPath { get; set; } = "";
    public string AllCategories { get; set; } = "";
    public decimal? WebsitePrice { get; set; }
    public decimal? StorePrice { get; set; }
    public string Currency { get; set; } = "";
    public string Availability { get; set; } = "";
    public string Unit { get; set; } = "";
    public decimal? MinimumOrder { get; set; }
    public decimal? OrderMultiple { get; set; }
    public decimal? WebsiteOrderLimit { get; set; }
    public string Description { get; set; } = "";
    public string SpecificationsJson { get; set; } = "{}";
    public string ImageUrls { get; set; } = "";
    public string DocumentUrls { get; set; } = "";
    public string ProductUrl { get; set; } = "";
    public string SourceUrls { get; set; } = "";
    public DateTimeOffset RetrievedAtUtc { get; set; }
}
