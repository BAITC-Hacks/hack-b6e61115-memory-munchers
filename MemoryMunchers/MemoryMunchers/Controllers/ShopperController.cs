using MemoryMunchers.Shopping;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;

namespace MemoryMunchers.Controllers;

[ApiController, Route("api/shopper")]
public sealed class ShopperController(ShopperTokenService tokens, ShopperDataService data) : ControllerBase
{
    [HttpPost, EnableRateLimiting("shopper")]
    public IActionResult Create()
    {
        var (_, token) = tokens.Issue();
        return Ok(new { token });
    }

    [HttpDelete, EnableRateLimiting("basket")]
    public async Task<IActionResult> Forget(CancellationToken token)
    {
        await data.ForgetAsync(token);
        return NoContent();
    }
}
