using System.ComponentModel.DataAnnotations;
using MemoryMunchers.Agents;
using Microsoft.AspNetCore.Mvc;

namespace MemoryMunchers.Controllers;

[ApiController]
[Route("api/agent-sessions")]
public sealed class AgentSessionsController(IAgentSessionService sessions, IAgentRunner runner) : ControllerBase
{
    [HttpPost]
    public async Task<ActionResult<AgentSessionDetails>> Create(CreateAgentSessionRequest request, CancellationToken cancellationToken)
    {
        var session = await sessions.CreateAsync(request.AgentId, request.Title, request.AdditionalInstructions, cancellationToken);
        return CreatedAtAction(nameof(Get), new { sessionId = session.Id }, session);
    }

    [HttpGet]
    public async Task<ActionResult<IReadOnlyList<AgentSessionSummary>>> List(
        CancellationToken cancellationToken, [FromQuery, Range(0, int.MaxValue)] int skip = 0,
        [FromQuery, Range(1, 100)] int take = 50) => Ok(await sessions.ListAsync(skip, take, cancellationToken));

    [HttpGet("{sessionId:guid}")]
    public async Task<ActionResult<AgentSessionDetails>> Get(Guid sessionId, CancellationToken cancellationToken) =>
        Ok(await sessions.GetAsync(sessionId, cancellationToken));

    [HttpPost("{sessionId:guid}/runs")]
    public async Task<ActionResult<AgentRunResult>> Run(Guid sessionId, RunAgentRequest request, CancellationToken cancellationToken) =>
        Ok(await runner.RunAsync(sessionId, request.Message, cancellationToken));
}

public sealed record CreateAgentSessionRequest(
    [property: Required, StringLength(100)] string AgentId,
    [property: StringLength(200)] string? Title = null,
    [property: StringLength(8000)] string? AdditionalInstructions = null);

public sealed record RunAgentRequest([property: Required, StringLength(32000)] string Message);
