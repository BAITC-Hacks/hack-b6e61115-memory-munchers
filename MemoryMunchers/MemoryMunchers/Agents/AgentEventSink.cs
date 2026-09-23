namespace MemoryMunchers.Agents;

// One scoped sink per HTTP request. No shared HttpResponse or DbContext between runs.
public sealed class AgentEventSink
{
    public Func<string, object, CancellationToken, Task>? Write { get; set; }
    public Task PublishAsync(string kind, object payload, CancellationToken token) => Write?.Invoke(kind, payload, token) ?? Task.CompletedTask;
}
