using System.Globalization;
using System.Text;
using MemoryMunchers.Products;

namespace MemoryMunchers.Tests;

public sealed class ProductCatalogReaderTests
{
    private const string Header = "ID товара;Код товара;Артикул поставщика;Наименование;Бренд;Категория;Путь категории;Все категории;Цена на сайте;Цена в магазине;Валюта;Статус на сайте;Единица;Минимум заказа;Кратность заказа;Лимит заказа на сайте;Описание;Характеристики JSON;Изображения URL;Документы URL;Карточка URL;Источники URL;Получено UTC";

    [Fact]
    public void PreservesQuotedMultilineFieldsAndParsesNullableNumbersIndependentlyOfCulture()
    {
        var previousCulture = CultureInfo.CurrentCulture;
        try
        {
            CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("ru-RU");
            var fields = Fields();
            fields[3] = "Реле; \"А\"";
            fields[7] = "Категория\nКатегория > Реле";
            fields[16] = "Первая строка; описание\r\nВторая строка";
            fields[17] = "{\"Описание\":\"Реле; А\"}";
            fields[21] = "https://example.test/one\nhttps://example.test/two";
            var product = Assert.Single(Parse(Header + "\r\n" + Row(fields)));

            Assert.Equal(41099, product.Id);
            Assert.Equal(fields[3], product.Name);
            Assert.Equal(fields[7], product.AllCategories);
            Assert.Equal(fields[16], product.Description);
            Assert.Equal(fields[17], product.SpecificationsJson);
            Assert.Equal(fields[21], product.SourceUrls);
            Assert.Equal(1234.56m, product.WebsitePrice);
            Assert.Null(product.StorePrice);
            Assert.Null(product.MinimumOrder);
            Assert.Equal(1m, product.OrderMultiple);
            Assert.Equal(TimeSpan.Zero, product.RetrievedAtUtc.Offset);
            Assert.Equal(3, product.RetrievedAtUtc.Hour);
        }
        finally { CultureInfo.CurrentCulture = previousCulture; }
    }

    [Fact]
    public void RejectsDuplicateProductIds()
    {
        var row = Row(Fields());
        Assert.Throws<InvalidDataException>(() => Parse(Header + "\n" + row + "\n" + row));
    }

    [Theory]
    [InlineData(0, "not-an-id")]
    [InlineData(0, "0")]
    [InlineData(3, "")]
    [InlineData(8, "1,23")]
    [InlineData(17, "{invalid}")]
    [InlineData(17, "[]")]
    [InlineData(22, "not-a-date")]
    public void RejectsInvalidProductsBeforeTheyCanBeImported(int column, string value)
    {
        var fields = Fields();
        fields[column] = value;
        var exception = Assert.Throws<InvalidDataException>(() => Parse(Header + "\n" + Row(fields)));
        Assert.Contains("line 2", exception.Message);
    }

    [Fact]
    public void RejectsTruncatedRows()
    {
        Assert.Throws<InvalidDataException>(() => Parse(Header + "\n41099;code"));
    }

    [Theory]
    [InlineData("")]
    [InlineData("ID товара;Наименование\n1;Test")]
    public void RejectsEmptyOrIncompleteHeaders(string csv)
    {
        Assert.Throws<InvalidDataException>(() => Parse(csv));
    }

    [Fact]
    public void RejectsACatalogWithoutProducts()
    {
        Assert.Throws<InvalidDataException>(() => Parse(Header));
    }

    [Fact]
    public void HonorsCancellation()
    {
        using var stream = new MemoryStream(Encoding.UTF8.GetBytes(Header + "\n" + Row(Fields())));
        Assert.Throws<OperationCanceledException>(() => ProductCatalogReader.Parse(stream, new CancellationToken(true)));
    }

    private static IReadOnlyList<Product> Parse(string csv)
    {
        using var stream = new MemoryStream(Encoding.UTF8.GetBytes("\uFEFF" + csv));
        return ProductCatalogReader.Parse(stream);
    }

    private static string Row(string[] fields) => string.Join(";", fields.Select(field => "\"" + field.Replace("\"", "\"\"") + "\""));

    private static string[] Fields() =>
    [
        "41099", "311100865_", "000-00-8-994", "Автомат", "F&F", "Автоматизация",
        "Автоматизация > Реле", "Автоматизация", "1234.56", "", "KZT", "Под заказ", "шт",
        "", "1", "20", "Описание", "{}", "", "", "https://example.test/product", "",
        "2026-09-23T08:41:00+05:00"
    ];
}
