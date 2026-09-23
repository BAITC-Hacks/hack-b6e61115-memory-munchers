using MemoryMunchers.Agents;
using Microsoft.AspNetCore.Mvc;

namespace MemoryMunchers.Controllers;

[ApiController]
[Route("api/agents")]
public sealed class AgentsController(IAgentCatalog catalog) : ControllerBase
{
    [HttpGet]
    public ActionResult<IReadOnlyCollection<AgentDefinition>> List() => Ok(catalog.GetAll());

    [HttpGet("{agentId}")]
    public ActionResult<AgentDefinition> Get(string agentId) => Ok(catalog.Get(agentId));
}
