using System.Globalization;
using System.Text;
using System.Text.Json;
using Microsoft.VisualBasic.FileIO;

namespace MemoryMunchers.Products;

public sealed class ProductCatalogReader(IWebHostEnvironment environment)
{
    public const string FileName = "nursultan_ekt_catalog.csv";

    public IReadOnlyList<Product> Read(CancellationToken cancellationToken)
    {
        var path = Path.Combine(environment.ContentRootPath, FileName);
        using var stream = File.OpenRead(path);
        return Parse(stream, cancellationToken);
    }

    public static IReadOnlyList<Product> Parse(Stream stream, CancellationToken cancellationToken = default)
    {
        using var parser = new TextFieldParser(stream, Encoding.UTF8, detectEncoding: true, leaveOpen: true)
        {
            TextFieldType = FieldType.Delimited,
            HasFieldsEnclosedInQuotes = true,
            TrimWhiteSpace = false
        };
        parser.SetDelimiters(";");

        string[] requiredHeaders =
        [
            "ID товара", "Код товара", "Артикул поставщика", "Наименование", "Бренд", "Категория",
            "Путь категории", "Все категории", "Цена на сайте", "Цена в магазине", "Валюта",
            "Статус на сайте", "Единица", "Минимум заказа", "Кратность заказа", "Лимит заказа на сайте",
            "Описание", "Характеристики JSON", "Изображения URL", "Документы URL", "Карточка URL",
            "Источники URL", "Получено UTC"
        ];
        var headers = parser.ReadFields() ?? throw new InvalidDataException("The catalog CSV is empty.");
        if (headers.Distinct(StringComparer.Ordinal).Count() != headers.Length ||
            requiredHeaders.Any(header => !headers.Contains(header, StringComparer.Ordinal)))
            throw new InvalidDataException("The catalog CSV is missing required columns or has duplicate headers.");

        var columns = headers.Select((header, index) => (header, index))
            .ToDictionary(column => column.header, column => column.index, StringComparer.Ordinal);
        var products = new List<Product>();
        var ids = new HashSet<int>();
        while (!parser.EndOfData)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var line = parser.LineNumber;
            try
            {
                var fields = parser.ReadFields()!;
                if (fields.Length != headers.Length)
                    throw new FormatException("The number of fields does not match the header.");

                string Field(string header) => fields[columns[header]];
                decimal? Number(string header) => string.IsNullOrWhiteSpace(Field(header))
                    ? null
                    : decimal.Parse(Field(header), NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
                        CultureInfo.InvariantCulture);

                var id = int.Parse(Field("ID товара"), CultureInfo.InvariantCulture);
                if (id <= 0 || !ids.Add(id)) throw new FormatException("Product IDs must be positive and unique.");
                if (string.IsNullOrWhiteSpace(Field("Наименование"))) throw new FormatException("A product name is required.");

                var specifications = string.IsNullOrWhiteSpace(Field("Характеристики JSON"))
                    ? "{}" : Field("Характеристики JSON");
                using var json = JsonDocument.Parse(specifications);
                if (json.RootElement.ValueKind != JsonValueKind.Object)
                    throw new FormatException("Product specifications must be a JSON object.");

                products.Add(new Product
                {
                    Id = id,
                    Code = Field("Код товара"),
                    SupplierArticle = Field("Артикул поставщика"),
                    Name = Field("Наименование"),
                    Brand = Field("Бренд"),
                    Category = Field("Категория"),
                    CategoryPath = Field("Путь категории"),
                    AllCategories = Field("Все категории"),
                    WebsitePrice = Number("Цена на сайте"),
                    StorePrice = Number("Цена в магазине"),
                    Currency = Field("Валюта"),
                    Availability = Field("Статус на сайте"),
                    Unit = Field("Единица"),
                    MinimumOrder = Number("Минимум заказа"),
                    OrderMultiple = Number("Кратность заказа"),
                    WebsiteOrderLimit = Number("Лимит заказа на сайте"),
                    Description = Field("Описание"),
                    SpecificationsJson = specifications,
                    ImageUrls = Field("Изображения URL"),
                    DocumentUrls = Field("Документы URL"),
                    ProductUrl = Field("Карточка URL"),
                    SourceUrls = Field("Источники URL"),
                    RetrievedAtUtc = DateTimeOffset.Parse(Field("Получено UTC"), CultureInfo.InvariantCulture,
                        DateTimeStyles.AssumeUniversal).ToUniversalTime()
                });
            }
            catch (Exception exception) when (exception is FormatException or OverflowException or JsonException or MalformedLineException)
            {
                throw new InvalidDataException($"The catalog CSV contains an invalid product at line {line}: {exception.Message}", exception);
            }
        }

        if (products.Count == 0) throw new InvalidDataException("The catalog CSV contains no products.");
        return products;
    }
}
