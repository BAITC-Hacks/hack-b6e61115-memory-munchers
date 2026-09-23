using System.Security.Cryptography;
using System.Text;
using MemoryMunchers.Products;
using Microsoft.AspNetCore.Mvc;
using Microsoft.VisualBasic.FileIO;

namespace MemoryMunchers.Controllers;

// Merchant feed uploads. Disabled unless Admin:ApiKey is configured; the key is sent in the X-Admin-Key header.
[ApiController, Route("api/admin")]
public sealed class AdminFeedsController(MerchantFeedImporter importer, IConfiguration configuration) : ControllerBase
{
    [HttpPost("inventory"), RequestSizeLimit(50 * 1024 * 1024)]
    public Task<IActionResult> Inventory(IFormFile file, CancellationToken token) =>
        ImportAsync(file, importer.ImportInventoryAsync, token);

    [HttpPost("certificates"), RequestSizeLimit(50 * 1024 * 1024)]
    public Task<IActionResult> Certificates(IFormFile file, CancellationToken token) =>
        ImportAsync(file, importer.ImportCertificatesAsync, token);

    private async Task<IActionResult> ImportAsync(IFormFile file, Func<Stream, CancellationToken, Task<FeedImportResult>> import,
        CancellationToken token)
    {
        var expected = configuration["Admin:ApiKey"];
        if (string.IsNullOrEmpty(expected)) return NotFound();
        var actual = Request.Headers["X-Admin-Key"].ToString();
        if (!CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(actual), Encoding.UTF8.GetBytes(expected)))
            return Unauthorized();
        try
        {
            await using var stream = file.OpenReadStream();
            return Ok(await import(stream, token));
        }
        catch (Exception error) when (error is InvalidDataException or FormatException or MalformedLineException)
        {
            return Problem(statusCode: StatusCodes.Status422UnprocessableEntity, title: "Invalid feed", detail: error.Message);
        }
    }
}
