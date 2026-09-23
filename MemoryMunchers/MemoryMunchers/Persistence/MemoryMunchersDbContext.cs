using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Persistence;

/// <summary>
/// EF Core database context for the Memory Munchers API.
/// Stores forecasts submitted to the API.
/// </summary>
public sealed class MemoryMunchersDbContext(DbContextOptions<MemoryMunchersDbContext> options)
    : DbContext(options)
{
    public DbSet<WeatherForecast> WeatherForecasts => Set<WeatherForecast>();
}
