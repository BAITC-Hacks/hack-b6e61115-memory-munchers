using System.ComponentModel.DataAnnotations;
using MemoryMunchers.Agents;
using MemoryMunchers.Shopping;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;

namespace MemoryMunchers.Controllers;

[ApiController, Route("api/basket"), EnableRateLimiting("basket")]
public sealed class BasketController(BasketService basket) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> Get(CancellationToken token) => Ok(await basket.GetAsync(token));
    [HttpPost("proposals/{id:guid}/confirm")]
    public async Task<IActionResult> Confirm(Guid id, CancellationToken token) => Ok(await basket.ConfirmAsync(id, token));
    [HttpPost("proposals/{id:guid}/cancel")]
    public async Task<IActionResult> Cancel(Guid id, CancellationToken token) => Ok(await basket.CancelAsync(id, token));
    [HttpPost("proposals/{id:guid}/refresh")]
    public async Task<IActionResult> Refresh(Guid id, CancellationToken token) => Ok(await basket.RefreshAsync(id, token));
    [HttpPatch("items/{productId:int}")]
    public async Task<IActionResult> Update(int productId, BasketQuantityRequest request, CancellationToken token)
    {
        try { return Ok(await basket.SetQuantityAsync(productId, request.Quantity, request.Version, token)); }
        catch (AgentToolInputException error) { throw new AgentException("invalid_quantity", error.Message, 422); }
    }
    [HttpDelete("items/{productId:int}")]
    public async Task<IActionResult> Remove(int productId, [FromQuery, Range(0, int.MaxValue)] int version, CancellationToken token) =>
        Ok(await basket.SetQuantityAsync(productId, 0, version, token));
}
public sealed record BasketQuantityRequest(decimal Quantity, [Range(0, int.MaxValue)] int Version);
