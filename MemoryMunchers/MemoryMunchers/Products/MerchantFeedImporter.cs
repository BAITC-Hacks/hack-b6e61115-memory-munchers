using System.Globalization;
using System.Text;
using MemoryMunchers.Persistence;
using MemoryMunchers.Shopping;
using Microsoft.EntityFrameworkCore;
using Microsoft.VisualBasic.FileIO;

namespace MemoryMunchers.Products;

/// <summary>
/// Imports merchant feeds (e.g. a 1C export) that are not part of the public catalog snapshot:
/// stock quantities and the certificate registry. Both are semicolon-separated CSV files keyed by "ID товара".
/// </summary>
public sealed class MerchantFeedImporter(MemoryMunchersDbContext db, TimeProvider clock)
{
    public const string DemoInventoryFile = "demo_inventory.csv";
    public const string DemoCertificatesFile = "demo_certificates.csv";

    // Columns: "ID товара;Остаток[;Проверено UTC]". Rows without a timestamp are stamped with the import time.
    public async Task<FeedImportResult> ImportInventoryAsync(Stream csv, CancellationToken token)
    {
        var rows = Read(csv, ["ID товара", "Остаток"], token);
        var now = clock.GetUtcNow();
        var parsed = rows.Select(r => (Id: Int(r, "ID товара"), Quantity: Decimal(r, "Остаток"),
            CheckedAt: r.TryGetValue("Проверено UTC", out var at) && !string.IsNullOrWhiteSpace(at)
                ? DateTimeOffset.Parse(at, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal).ToUniversalTime() : now)).ToArray();
        if (parsed.Any(r => r.Quantity < 0)) throw new InvalidDataException("Остаток не может быть отрицательным.");
        var known = await KnownAsync(parsed.Select(r => r.Id), token);
        var accepted = parsed.Where(r => known.Contains(r.Id)).GroupBy(r => r.Id).Select(g => g.Last()).ToArray();
        await db.Database.ExecuteSqlInterpolatedAsync($"""
            INSERT INTO "ProductInventory" ("ProductId", "AvailableQuantity", "CheckedAt")
            SELECT * FROM unnest({accepted.Select(r => r.Id).ToArray()}, {accepted.Select(r => r.Quantity).ToArray()}, {accepted.Select(r => r.CheckedAt).ToArray()})
            ON CONFLICT ("ProductId") DO UPDATE SET "AvailableQuantity" = EXCLUDED."AvailableQuantity", "CheckedAt" = EXCLUDED."CheckedAt"
            """, token);
        return new(accepted.Length, parsed.Length - accepted.Length);
    }

    // Columns: "ID товара;Номер;Тип;Выдан;Действует до;URL;Файл". Replaces the certificates of every product in the file.
    public async Task<FeedImportResult> ImportCertificatesAsync(Stream csv, CancellationToken token)
    {
        var rows = Read(csv, ["ID товара", "Номер", "Тип"], token);
        string Optional(IReadOnlyDictionary<string, string> r, string key) => r.TryGetValue(key, out var v) ? v.Trim() : "";
        var parsed = rows.Select(r => new ProductCertificate
        {
            ProductId = Int(r, "ID товара"), Number = r["Номер"].Trim(), Type = r["Тип"].Trim(), IssuedBy = Optional(r, "Выдан"),
            ValidUntil = Optional(r, "Действует до") is { Length: > 0 } date ? DateOnly.Parse(date, CultureInfo.InvariantCulture) : null,
            Url = ShopJson.SafeUrl(Optional(r, "URL")),
            FileName = Optional(r, "Файл") is { Length: > 0 } file ? Path.GetFileName(file) : null
        }).ToArray();
        if (parsed.Any(c => c.Number.Length is 0 or > 200 || c.Type.Length is 0 or > 300 || c.IssuedBy.Length > 300))
            throw new InvalidDataException("Номер и тип сертификата обязательны (до 200/300 символов).");
        var known = await KnownAsync(parsed.Select(c => c.ProductId), token);
        var accepted = parsed.Where(c => known.Contains(c.ProductId)).ToArray();
        var ids = accepted.Select(c => c.ProductId).Distinct().ToArray();
        await using var transaction = await db.Database.BeginTransactionAsync(token);
        await db.ProductCertificates.Where(c => ids.Contains(c.ProductId)).ExecuteDeleteAsync(token);
        foreach (var batch in accepted.Chunk(500))
        {
            db.ProductCertificates.AddRange(batch);
            await db.SaveChangesAsync(token);
            db.ChangeTracker.Clear();
        }
        await transaction.CommitAsync(token);
        return new(accepted.Length, parsed.Length - accepted.Length);
    }

    private async Task<HashSet<int>> KnownAsync(IEnumerable<int> ids, CancellationToken token)
    {
        var keys = ids.Distinct().ToArray();
        return await db.Products.Where(p => keys.Contains(p.Id)).Select(p => p.Id).ToHashSetAsync(token);
    }

    private static List<Dictionary<string, string>> Read(Stream stream, string[] required, CancellationToken token)
    {
        using var parser = new TextFieldParser(stream, Encoding.UTF8, detectEncoding: true, leaveOpen: true)
            { TextFieldType = FieldType.Delimited, HasFieldsEnclosedInQuotes = true };
        parser.SetDelimiters(";");
        var headers = parser.ReadFields() ?? throw new InvalidDataException("Файл пуст.");
        if (required.Any(h => !headers.Contains(h))) throw new InvalidDataException($"Нужны колонки: {string.Join(", ", required)}.");
        var rows = new List<Dictionary<string, string>>();
        while (!parser.EndOfData)
        {
            token.ThrowIfCancellationRequested();
            var line = parser.LineNumber;
            var fields = parser.ReadFields()!;
            if (fields.Length != headers.Length) throw new InvalidDataException($"Строка {line}: число полей не совпадает с заголовком.");
            rows.Add(headers.Select((h, i) => (h, fields[i])).ToDictionary(x => x.h, x => x.Item2));
            if (rows.Count > 100_000) throw new InvalidDataException("Не более 100 000 строк за импорт.");
        }
        return rows;
    }

    private static int Int(IReadOnlyDictionary<string, string> row, string key) =>
        int.TryParse(row[key], NumberStyles.None, CultureInfo.InvariantCulture, out var value) && value > 0
            ? value : throw new InvalidDataException($"Некорректное значение «{key}»: {row[key]}.");
    private static decimal Decimal(IReadOnlyDictionary<string, string> row, string key) =>
        decimal.TryParse(row[key], NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out var value)
            ? value : throw new InvalidDataException($"Некорректное значение «{key}»: {row[key]}.");
}

public sealed record FeedImportResult(int Imported, int SkippedUnknownProducts);

/// <summary>Simulates the merchant's inventory/certificate feed for demos: imports the demo files on start and every 6 hours.</summary>
public sealed class DemoFeedService(IServiceScopeFactory scopes, IWebHostEnvironment environment,
    ILogger<DemoFeedService> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromHours(6));
        var certificatesLoaded = false;
        do
        {
            try
            {
                await using var scope = scopes.CreateAsyncScope();
                // Stock rows are only accepted for products that exist, so make sure the catalog is imported first.
                await scope.ServiceProvider.GetRequiredService<ProductCatalogService>().ImportAsync(stoppingToken);
                var importer = scope.ServiceProvider.GetRequiredService<MerchantFeedImporter>();
                await using (var inventory = File.OpenRead(Path.Combine(environment.ContentRootPath, MerchantFeedImporter.DemoInventoryFile)))
                    await importer.ImportInventoryAsync(inventory, stoppingToken);
                if (!certificatesLoaded)
                {
                    await using var certificates = File.OpenRead(Path.Combine(environment.ContentRootPath, MerchantFeedImporter.DemoCertificatesFile));
                    await importer.ImportCertificatesAsync(certificates, stoppingToken);
                    certificatesLoaded = true;
                }
                logger.LogInformation("Demo inventory and certificate feeds imported");
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { return; }
            catch (Exception error) { logger.LogWarning(error, "Demo feed import failed"); }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
