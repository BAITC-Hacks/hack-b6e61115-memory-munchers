using MemoryMunchers.Agents.Persistence;
using MemoryMunchers.Products;
using Microsoft.EntityFrameworkCore;
using MemoryMunchers.Shopping;

namespace MemoryMunchers.Persistence;

/// <summary>
/// EF Core database context for the Memory Munchers API.
/// Stores products and durable agent conversations.
/// </summary>
public sealed class MemoryMunchersDbContext(DbContextOptions<MemoryMunchersDbContext> options)
    : DbContext(options)
{
    public DbSet<AgentSession> AgentSessions => Set<AgentSession>();
    public DbSet<AgentRun> AgentRuns => Set<AgentRun>();
    public DbSet<Product> Products => Set<Product>();
    public DbSet<Basket> Baskets => Set<Basket>();
    public DbSet<BasketProposal> BasketProposals => Set<BasketProposal>();
    public DbSet<ProductInventory> ProductInventory => Set<ProductInventory>();
    public DbSet<ChatAttachment> ChatAttachments => Set<ChatAttachment>();
    public DbSet<ChatEvent> ChatEvents => Set<ChatEvent>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.HasPostgresExtension("pg_trgm");
        modelBuilder.Entity<Basket>().HasMany(b => b.Items).WithOne().HasForeignKey(i => i.BasketId).OnDelete(DeleteBehavior.Cascade);
        modelBuilder.Entity<Basket>().Property(b => b.Id).ValueGeneratedNever();
        modelBuilder.Entity<BasketItem>().HasKey(i => new { i.BasketId, i.ProductId });
        modelBuilder.Entity<BasketItem>().Property(i => i.Quantity).HasPrecision(18, 3);
        modelBuilder.Entity<ProductInventory>().HasKey(i => i.ProductId);
        modelBuilder.Entity<ProductInventory>().Property(i => i.ProductId).ValueGeneratedNever();
        modelBuilder.Entity<BasketProposal>().Property(p => p.LinesJson).HasColumnType("jsonb");
        modelBuilder.Entity<BasketProposal>().HasIndex(p => new { p.RunId, p.CallId }).IsUnique();
        modelBuilder.Entity<BasketProposal>().HasIndex(p => new { p.ShopperId, p.SessionId });
        modelBuilder.Entity<ChatAttachment>().HasIndex(a => a.ExpiresAt);
        modelBuilder.Entity<ChatEvent>().Property(e => e.PayloadJson).HasColumnType("jsonb");
        modelBuilder.Entity<ChatEvent>().HasIndex(e => new { e.SessionId, e.CreatedAt });
        modelBuilder.Entity<Product>(entity =>
        {
            entity.HasKey(product => product.Id);
            entity.Property(product => product.Id).ValueGeneratedNever();
            entity.Property(product => product.SpecificationsJson).HasColumnType("jsonb");
            entity.Property(product => product.SearchText).HasComputedColumnSql("\"Name\" || ' ' || \"Brand\" || ' ' || \"Code\" || ' ' || \"SupplierArticle\" || ' ' || \"CategoryPath\" || ' ' || \"SpecificationsJson\"::text", stored: true);
            entity.HasIndex(product => product.SearchText).HasMethod("gin").HasOperators("gin_trgm_ops");
            entity.HasIndex(product => product.Code);
            entity.HasIndex(product => product.SupplierArticle);
            entity.HasIndex(product => product.Category);
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
            entity.HasIndex(session => new { session.ShopperId, session.UpdatedAt });
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
            entity.HasIndex(run => new { run.SessionId, run.ClientRequestId }).IsUnique();
        });
    }
}
