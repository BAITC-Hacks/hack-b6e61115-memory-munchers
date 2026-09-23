using MemoryMunchers.Agents.Persistence;
using MemoryMunchers.Products;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Persistence;

/// <summary>
/// EF Core database context for the Memory Munchers API.
/// Stores forecasts, products, and durable agent conversations.
/// </summary>
public sealed class MemoryMunchersDbContext(DbContextOptions<MemoryMunchersDbContext> options)
    : DbContext(options)
{
    public DbSet<WeatherForecast> WeatherForecasts => Set<WeatherForecast>();
    public DbSet<AgentSession> AgentSessions => Set<AgentSession>();
    public DbSet<AgentRun> AgentRuns => Set<AgentRun>();
    public DbSet<Product> Products => Set<Product>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Product>(entity =>
        {
            entity.HasKey(product => product.Id);
            entity.Property(product => product.Id).ValueGeneratedNever();
            entity.Property(product => product.SpecificationsJson).HasColumnType("jsonb");
        });
        modelBuilder.Entity<AgentSession>(entity =>
        {
            entity.HasKey(session => session.Id);
            entity.Property(session => session.Id).ValueGeneratedNever();
            entity.Property(session => session.AgentId).HasMaxLength(100);
            entity.Property(session => session.Title).HasMaxLength(200);
            entity.Property(session => session.Model).HasMaxLength(200);
            entity.Property(session => session.ToolNamesJson).HasColumnType("jsonb");
            entity.Property(session => session.HistoryJson).HasColumnType("jsonb");
            entity.HasIndex(session => new { session.UpdatedAt, session.Id });
            entity.HasMany(session => session.Runs).WithOne().HasForeignKey(run => run.SessionId)
                .OnDelete(DeleteBehavior.Cascade);
        });
        modelBuilder.Entity<AgentRun>(entity =>
        {
            entity.HasKey(run => run.Id);
            entity.Property(run => run.Id).ValueGeneratedNever();
            entity.Property(run => run.Status).HasConversion<string>().HasMaxLength(20);
            entity.Property(run => run.ErrorCode).HasMaxLength(100);
            entity.HasIndex(run => new { run.SessionId, run.StartedAt });
        });
    }
}
