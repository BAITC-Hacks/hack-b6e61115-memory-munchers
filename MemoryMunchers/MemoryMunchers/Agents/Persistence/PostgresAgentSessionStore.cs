using MemoryMunchers.Persistence;
using Microsoft.EntityFrameworkCore;
using MemoryMunchers.Shopping;

namespace MemoryMunchers.Agents.Persistence;

public sealed class PostgresAgentSessionStore(MemoryMunchersDbContext db, ShopperContext shopper) : IAgentSessionStore
{
    public async Task AddAsync(AgentSession session, CancellationToken cancellationToken)
    {
        db.AgentSessions.Add(session);
        await db.SaveChangesAsync(cancellationToken);
    }

    public Task<AgentSession?> FindAsync(Guid sessionId, CancellationToken cancellationToken) =>
        db.AgentSessions.Include(session => session.Runs)
            .SingleOrDefaultAsync(session => session.Id == sessionId && session.ShopperId == shopper.Id, cancellationToken);

    public async Task<IReadOnlyList<AgentSessionSummary>> ListAsync(int skip, int take,
        CancellationToken cancellationToken) => await db.AgentSessions.AsNoTracking().Where(session => session.ShopperId == shopper.Id)
        .OrderByDescending(session => session.UpdatedAt).ThenBy(session => session.Id)
        .Skip(skip).Take(take)
        .Select(session => new AgentSessionSummary(session.Id, session.AgentId, session.Title,
            session.CreatedAt, session.UpdatedAt))
        .ToListAsync(cancellationToken);

    public Task SaveAsync(CancellationToken cancellationToken) => db.SaveChangesAsync(cancellationToken);
}
