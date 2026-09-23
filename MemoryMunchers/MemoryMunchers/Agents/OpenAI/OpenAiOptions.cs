namespace MemoryMunchers.Agents.OpenAI;

public sealed class OpenAiOptions
{
    public const string SectionName = "OpenAI";
    public string ApiKey { get; set; } = "";
    public string Model { get; set; } = "gpt-6-sol";
    public int MaxOutputTokens { get; set; } = 128_000;
    public string ReasoningEffort { get; set; } = "max";
    public int RequestTimeoutSeconds { get; set; } = 600;
}
