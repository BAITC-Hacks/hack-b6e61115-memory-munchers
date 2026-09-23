using MemoryMunchers.Persistence;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Agents.Persistence;

public sealed class PostgresAgentSessionStore(MemoryMunchersDbContext db) : IAgentSessionStore
{
    public async Task AddAsync(AgentSession session, CancellationToken cancellationToken)
    {
        db.AgentSessions.Add(session);
        await db.SaveChangesAsync(cancellationToken);
    }

    public Task<AgentSession?> FindAsync(Guid sessionId, CancellationToken cancellationToken) =>
        db.AgentSessions.Include(session => session.Runs)
            .SingleOrDefaultAsync(session => session.Id == sessionId, cancellationToken);

    public async Task<IReadOnlyList<AgentSessionSummary>> ListAsync(int skip, int take,
        CancellationToken cancellationToken) => await db.AgentSessions.AsNoTracking()
        .OrderByDescending(session => session.UpdatedAt).ThenBy(session => session.Id)
        .Skip(skip).Take(take)
        .Select(session => new AgentSessionSummary(session.Id, session.AgentId, session.Title,
            session.CreatedAt, session.UpdatedAt))
        .ToListAsync(cancellationToken);

    public Task SaveAsync(CancellationToken cancellationToken) => db.SaveChangesAsync(cancellationToken);
}
