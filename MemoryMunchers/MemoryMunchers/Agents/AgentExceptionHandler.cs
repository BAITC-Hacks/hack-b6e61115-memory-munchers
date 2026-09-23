using Microsoft.AspNetCore.Diagnostics;
using Microsoft.AspNetCore.Mvc;

namespace MemoryMunchers.Agents;

public sealed class AgentExceptionHandler(IProblemDetailsService problemDetails) : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(HttpContext httpContext, Exception exception, CancellationToken cancellationToken)
    {
        if (exception is not AgentException agentException) return false;

        httpContext.Response.StatusCode = agentException.StatusCode;
        return await problemDetails.TryWriteAsync(new ProblemDetailsContext
        {
            HttpContext = httpContext,
            ProblemDetails = new ProblemDetails
            {
                Status = agentException.StatusCode,
                Title = agentException.Code,
                Detail = agentException.Message,
                Extensions =
                {
                    ["sessionId"] = agentException.SessionId,
                    ["runId"] = agentException.RunId
                }
            }
        });
    }
}
