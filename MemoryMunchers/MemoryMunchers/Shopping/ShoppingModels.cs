using System.Text.Json;

namespace MemoryMunchers.Shopping;

public sealed class ShoppingOptions
{
    public bool RequireVerifiedStock { get; set; }
    public int InventoryMaxAgeMinutes { get; set; } = 15;
    public int ProposalLifetimeMinutes { get; set; } = 10;
    public int AttachmentLifetimeHours { get; set; } = 24;
    public int SessionRetentionDays { get; set; } = 30;
    public string? CheckoutUrl { get; set; }
    // Absolute URL of the basket page shown to the shopper after confirmation.
    public string? BasketUrl { get; set; }
    // Absolute API URL used to build certificate links for the widget and the agent.
    public string? PublicApiUrl { get; set; }
    public PurchaseConditionsOptions PurchaseConditions { get; set; } = new();
    public int MaxOutputTokens { get; set; } = 4096;
    public string ReasoningEffort { get; set; } = "low";
    public int RunTimeoutSeconds { get; set; } = 90;
}

public sealed class PurchaseConditionsOptions
{
    public string[] Payment { get; set; } = [];
    public string[] Delivery { get; set; } = [];
    public string[] Pickup { get; set; } = [];
    public string? MinimumOrder { get; set; }
    public string[] Returns { get; set; } = [];
    public string[] Notes { get; set; } = [];
    public string? SourceUrl { get; set; }
    public bool IsConfigured => Payment.Length + Delivery.Length + Pickup.Length + Returns.Length > 0 || MinimumOrder != null;
}

public sealed class Basket
{
    public Guid Id { get; set; }
    public int Version { get; set; }
    public List<BasketItem> Items { get; set; } = [];
}

public sealed class BasketItem
{
    public Guid BasketId { get; set; }
    public int ProductId { get; set; }
    public decimal Quantity { get; set; }
}

// Populated by the merchant's inventory feed. Catalog order limits are never stock quantities.
public sealed class ProductInventory
{
    public int ProductId { get; set; }
    public decimal AvailableQuantity { get; set; }
    public DateTimeOffset CheckedAt { get; set; }
}

// Populated by the merchant's certificate registry; demo rows are clearly labelled as such.
public sealed class ProductCertificate
{
    public int Id { get; set; }
    public int ProductId { get; set; }
    public string Number { get; set; } = "";
    public string Type { get; set; } = "";
    public string IssuedBy { get; set; } = "";
    public DateOnly? ValidUntil { get; set; }
    public string? Url { get; set; }
    public string? FileName { get; set; }
}

public sealed class BasketProposal
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid ShopperId { get; set; }
    public Guid SessionId { get; set; }
    public Guid RunId { get; set; }
    public string CallId { get; set; } = "";
    public int BasketVersion { get; set; }
    public string Status { get; set; } = "pending";
    public string LinesJson { get; set; } = "[]";
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset ExpiresAt { get; set; }
    public DateTimeOffset? ConfirmedAt { get; set; }
}

public sealed class ChatAttachment
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid ShopperId { get; set; }
    public Guid SessionId { get; set; }
    public string Name { get; set; } = "";
    public string ContentType { get; set; } = "";
    public byte[] Content { get; set; } = [];
    public string? ExtractedText { get; set; }
    public DateTimeOffset ExpiresAt { get; set; }
}

public sealed class ChatEvent
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid SessionId { get; set; }
    public Guid? RunId { get; set; }
    public string Kind { get; set; } = "";
    public string PayloadJson { get; set; } = "{}";
    public DateTimeOffset CreatedAt { get; set; }
}

public sealed record ProductCard(int Id, string Code, string Name, string Brand, string Category,
    decimal? Price, string Currency, string Availability, string Unit, decimal? MinimumOrder,
    decimal? OrderMultiple, decimal? WebsiteOrderLimit, decimal? AvailableQuantity,
    DateTimeOffset? StockCheckedAt, DateTimeOffset CatalogCheckedAt, string? ProductUrl,
    string[] DocumentUrls, JsonElement Specifications, string? Description = null,
    IReadOnlyList<CertificateView>? Certificates = null)
{
    // in_stock / out_of_stock come from a fresh inventory feed; otherwise only the catalog status is known.
    public string StockStatus => AvailableQuantity switch
    {
        > 0 => "in_stock",
        not null => "out_of_stock",
        _ => Availability == "Купить" ? "unknown_orderable" : "on_request"
    };
}
public sealed record CertificateView(int Id, string Number, string Type, string IssuedBy, DateOnly? ValidUntil, string? Url);
public sealed record RequestedItem(int ProductId, decimal Quantity);
public sealed record ProposalLine(ProductCard Product, decimal Quantity, decimal ResultingQuantity);
public sealed record ProposalView(Guid Id, Guid SessionId, string Status, DateTimeOffset ExpiresAt,
    IReadOnlyList<ProposalLine> Lines, string? BasketUrl = null);
public sealed record BasketLine(ProductCard Product, decimal Quantity, decimal? Total);
public sealed record BasketView(int Version, IReadOnlyList<BasketLine> Items,
    IReadOnlyDictionary<string, decimal> Totals, bool HasUnknownPrices, bool VerifiedStockRequired,
    string? CheckoutUrl, string? BasketUrl = null);

public static class ShopJson
{
    public static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web);
    public static string Write<T>(T value) => JsonSerializer.Serialize(value, Options);
    public static T Read<T>(string value) => JsonSerializer.Deserialize<T>(value, Options)!;
    public static string? SafeUrl(string? value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme is "http" or "https" && string.IsNullOrEmpty(uri.UserInfo) ? uri.AbsoluteUri : null;
}
