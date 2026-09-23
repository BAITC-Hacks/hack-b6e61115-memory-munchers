using System.Buffers.Binary;
using MemoryMunchers.Persistence;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace MemoryMunchers.Agents.Persistence;

/// <summary>
/// A dedicated connection holds a PostgreSQL advisory lock for the run without a long transaction.
/// PostgreSQL releases the lock if the process or connection dies. Different sessions can run concurrently.
/// </summary>
public sealed class PostgresAgentSessionLock(MemoryMunchersDbContext db) : IAgentSessionLock
{
    public async Task<IAsyncDisposable?> TryAcquireAsync(Guid sessionId, CancellationToken cancellationToken)
    {
        var connection = new NpgsqlConnection(db.Database.GetConnectionString());
        var key = BinaryPrimitives.ReadInt64LittleEndian(sessionId.ToByteArray());
        try
        {
            await connection.OpenAsync(cancellationToken);
            await using var command = new NpgsqlCommand("SELECT pg_try_advisory_lock(@key)", connection);
            command.Parameters.AddWithValue("key", key);
            if ((bool)(await command.ExecuteScalarAsync(cancellationToken))!)
                return new Lease(connection, key);

            await connection.DisposeAsync();
            return null;
        }
        catch
        {
            await connection.DisposeAsync();
            throw;
        }
    }

    private sealed class Lease(NpgsqlConnection connection, long key) : IAsyncDisposable
    {
        public async ValueTask DisposeAsync()
        {
            try
            {
                await using var command = new NpgsqlCommand("SELECT pg_advisory_unlock(@key)", connection);
                command.CommandTimeout = 5;
                command.Parameters.AddWithValue("key", key);
                await command.ExecuteScalarAsync();
            }
            finally
            {
                await connection.DisposeAsync();
            }
        }
    }
}
