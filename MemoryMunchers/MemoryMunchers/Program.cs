using MemoryMunchers.Agents;
using MemoryMunchers.Persistence;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddDbContext<MemoryMunchersDbContext>(options =>
    options.UseNpgsql(builder.Configuration.GetConnectionString("MemoryMunchers")));
builder.Services.AddAgenticWorkflow(builder.Configuration);
builder.Services.AddProblemDetails();
builder.Services.AddExceptionHandler<AgentExceptionHandler>();

// Add services to the container.

builder.Services.AddControllers();
builder.Services.AddCors(options =>
{
    options.AddPolicy("PoCDevelopment", policy =>
    {
        policy.AllowAnyOrigin()
            .AllowAnyHeader()
            .AllowAnyMethod();
    });
});
builder.Services.AddSwaggerGen(options =>
{
    options.SwaggerDoc("v1", new Microsoft.OpenApi.OpenApiInfo
    {
        Title = "Memory Munchers API",
        Version = "v1"
    });
});

var app = builder.Build();

await using (var scope = app.Services.CreateAsyncScope())
{
    var catalog = scope.ServiceProvider.GetRequiredService<IAgentCatalog>();
    var tools = scope.ServiceProvider.GetRequiredService<IAgentToolRegistry>();
    foreach (var agent in catalog.GetAll()) tools.Resolve(agent.ToolNames);
    var dbContext = scope.ServiceProvider.GetRequiredService<MemoryMunchersDbContext>();
    await dbContext.Database.MigrateAsync();
}

// Configure the HTTP request pipeline.
app.UseExceptionHandler();
if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI(options =>
    {
        options.SwaggerEndpoint("/swagger/v1/swagger.json", "Memory Munchers API v1");
    });
}

app.UseHttpsRedirection();

app.UseCors("PoCDevelopment");
app.UseAuthorization();

app.MapControllers();

app.Run();
