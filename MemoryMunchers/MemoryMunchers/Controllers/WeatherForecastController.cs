using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using MemoryMunchers.Persistence;

namespace MemoryMunchers.Controllers
{
    [ApiController]
    [Route("[controller]")]
    public class WeatherForecastController(MemoryMunchersDbContext dbContext) : ControllerBase
    {
        private static readonly string[] Summaries =
        [
            "Freezing", "Bracing", "Chilly", "Cool", "Mild", "Warm", "Balmy", "Hot", "Sweltering", "Scorching"
        ];

        [HttpGet(Name = "GetWeatherForecast")]
        public async Task<IEnumerable<WeatherForecast>> Get(CancellationToken cancellationToken)
        {
            var samples = Enumerable.Range(1, 5).Select(index => new WeatherForecast
            {
                Date = DateOnly.FromDateTime(DateTime.Now.AddDays(index)),
                TemperatureC = Random.Shared.Next(-20, 55),
                Summary = Summaries[Random.Shared.Next(Summaries.Length)]
            });
            var savedForecasts = await dbContext.WeatherForecasts
                .AsNoTracking()
                .OrderBy(forecast => forecast.Id)
                .ToListAsync(cancellationToken);

            return samples.Concat(savedForecasts).ToArray();
        }

        [HttpPost]
        public async Task<ActionResult<WeatherForecast>> Add(
            WeatherForecast forecast,
            CancellationToken cancellationToken)
        {
            forecast.Id = 0;
            dbContext.WeatherForecasts.Add(forecast);
            await dbContext.SaveChangesAsync(cancellationToken);

            return CreatedAtAction(nameof(Get), new { id = forecast.Id }, forecast);
        }

        [HttpDelete("{id:int}")]
        public async Task<IActionResult> Remove(int id, CancellationToken cancellationToken)
        {
            var forecast = await dbContext.WeatherForecasts.FindAsync([id], cancellationToken);
            if (forecast is null)
            {
                return NotFound();
            }

            dbContext.WeatherForecasts.Remove(forecast);
            await dbContext.SaveChangesAsync(cancellationToken);
            return NoContent();
        }
    }
}
