using System.ComponentModel.DataAnnotations;
using MemoryMunchers.Persistence;
using MemoryMunchers.Products;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Controllers;

[ApiController]
[Route("api/products")]
public sealed class ProductsController(MemoryMunchersDbContext db, ProductCatalogService catalog) : ControllerBase
{
    [HttpGet("status")]
    public Task<ActionResult<ProductCatalogStatus>> Status(CancellationToken cancellationToken) =>
        ReadCatalogAsync(() => catalog.GetStatusAsync(cancellationToken));

    [HttpPost("import")]
    public Task<ActionResult<ProductCatalogStatus>> Import(CancellationToken cancellationToken) =>
        ReadCatalogAsync(() => catalog.ImportAsync(cancellationToken));

    [HttpGet]
    public async Task<ActionResult<ProductPage>> List(CancellationToken cancellationToken,
        [FromQuery, Range(1, int.MaxValue)] int page = 1, [FromQuery] int pageSize = 20)
    {
        if (pageSize is not (20 or 50 or 100))
        {
            ModelState.AddModelError(nameof(pageSize), "Page size must be 20, 50, or 100.");
            return ValidationProblem(ModelState);
        }

        var totalCount = await db.Products.CountAsync(cancellationToken);
        var totalPages = (int)Math.Ceiling(totalCount / (double)pageSize);
        page = Math.Min(page, Math.Max(1, totalPages));
        var items = await db.Products.AsNoTracking().OrderBy(product => product.Id)
            .Skip((page - 1) * pageSize).Take(pageSize)
            .Select(product => new ProductSummary(product.Id, product.Code, product.SupplierArticle,
                product.Name, product.Brand, product.Category, product.CategoryPath, product.WebsitePrice,
                product.StorePrice, product.Currency, product.Availability, product.Unit, product.ProductUrl))
            .ToListAsync(cancellationToken);
        return Ok(new ProductPage(items, page, pageSize, totalCount, totalPages));
    }

    [HttpGet("{id:int}")]
    public async Task<ActionResult<Product>> Get(int id, CancellationToken cancellationToken)
    {
        var product = await db.Products.AsNoTracking().SingleOrDefaultAsync(product => product.Id == id, cancellationToken);
        return product is null ? NotFound() : Ok(product);
    }

    private async Task<ActionResult<ProductCatalogStatus>> ReadCatalogAsync(Func<Task<ProductCatalogStatus>> action)
    {
        try
        {
            return Ok(await action());
        }
        catch (FileNotFoundException)
        {
            return Problem(statusCode: StatusCodes.Status503ServiceUnavailable, title: "Catalog file not found",
                detail: $"Place {ProductCatalogReader.FileName} in the API content root and try again.");
        }
        catch (InvalidDataException exception)
        {
            return Problem(statusCode: StatusCodes.Status422UnprocessableEntity, title: "Invalid catalog file",
                detail: exception.Message);
        }
    }
}
