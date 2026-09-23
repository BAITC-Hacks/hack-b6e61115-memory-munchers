namespace MemoryMunchers.Agents.OpenAI;

public sealed class OpenAiOptions
{
    public const string SectionName = "OpenAI";
    public string ApiKey { get; set; } = "";
    public string Model { get; set; } = "gpt-4.1-mini";
    public int MaxOutputTokens { get; set; } = 4096;
    public int RequestTimeoutSeconds { get; set; } = 120;
}
