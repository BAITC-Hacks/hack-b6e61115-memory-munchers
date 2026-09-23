namespace MemoryMunchers.Agents;

public sealed class AgentException(string code, string message, int statusCode, Exception? innerException = null)
    : Exception(message, innerException)
{
    public string Code { get; } = code;
    public int StatusCode { get; } = statusCode;
    public Guid? SessionId { get; init; }
    public Guid? RunId { get; init; }
}

/// <summary>An expected input error whose message may safely be returned to the model.</summary>
public sealed class AgentToolInputException(string message) : Exception(message);
