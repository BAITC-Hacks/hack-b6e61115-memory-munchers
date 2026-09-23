using System.Net;
using MemoryMunchers.Persistence;
using MemoryMunchers.Shopping;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Controllers;

[ApiController, Route("api/certificates")]
public sealed class CertificatesController(MemoryMunchersDbContext db, IWebHostEnvironment environment) : ControllerBase
{
    public const string FilesFolder = "certificates";

    // Serves the certificate file when the registry has one, otherwise a printable certificate record.
    [HttpGet("{id:int}")]
    public async Task<IActionResult> Get(int id, CancellationToken token)
    {
        var certificate = await db.ProductCertificates.AsNoTracking().SingleOrDefaultAsync(c => c.Id == id, token);
        if (certificate == null) return NotFound();
        if (!string.IsNullOrEmpty(certificate.FileName))
        {
            var folder = Path.GetFullPath(Path.Combine(environment.ContentRootPath, FilesFolder));
            var path = Path.GetFullPath(Path.Combine(folder, Path.GetFileName(certificate.FileName)));
            if (path.StartsWith(folder + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) && System.IO.File.Exists(path))
                return PhysicalFile(path, path.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase) ? "application/pdf" : "application/octet-stream");
        }
        if (ShopJson.SafeUrl(certificate.Url) is { } url) return Redirect(url);
        var product = await db.Products.AsNoTracking().Where(p => p.Id == certificate.ProductId)
            .Select(p => new { p.Code, p.Name, p.Brand }).SingleOrDefaultAsync(token);
        string E(string? value) => WebUtility.HtmlEncode(value ?? "");
        var html = $$"""
            <!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Сертификат {{E(certificate.Number)}}</title>
            <style>body{font:15px/1.6 system-ui,sans-serif;max-width:720px;margin:32px auto;padding:0 16px;color:#172438}dt{color:#667}dd{margin:0 0 12px}</style></head>
            <body><h1>{{E(certificate.Type)}}</h1><dl>
            <dt>Номер</dt><dd>{{E(certificate.Number)}}</dd>
            <dt>Товар</dt><dd>{{E(product?.Name)}} (код {{E(product?.Code)}}, {{E(product?.Brand)}})</dd>
            <dt>Выдан</dt><dd>{{E(certificate.IssuedBy)}}</dd>
            <dt>Действует до</dt><dd>{{E(certificate.ValidUntil?.ToString("dd.MM.yyyy") ?? "не указано")}}</dd>
            </dl></body></html>
            """;
        return Content(html, "text/html; charset=utf-8");
    }
}
