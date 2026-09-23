using MemoryMunchers.Agents;
using MemoryMunchers.Persistence;
using MemoryMunchers.Products;
using Microsoft.EntityFrameworkCore;
using MemoryMunchers.Shopping;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddDbContext<MemoryMunchersDbContext>(options =>
    options.UseNpgsql(builder.Configuration.GetConnectionString("MemoryMunchers")));
builder.Services.AddAgenticWorkflow(builder.Configuration);
builder.Services.AddScoped<ProductCatalogReader>();
builder.Services.AddScoped<ProductCatalogService>();
builder.Services.AddScoped<ShopperContext>();
builder.Services.AddScoped<ProductLookup>();
builder.Services.AddScoped<BasketService>();
builder.Services.AddScoped<AttachmentService>();
builder.Services.AddScoped<ProductChatService>();
builder.Services.AddScoped<AgentEventSink>();
builder.Services.AddHostedService<AttachmentCleanup>();
builder.Services.AddOptions<ShoppingOptions>().BindConfiguration("Shopping")
    .Validate(o => o.MaxOutputTokens is >= 128 and <= 16384 && o.RunTimeoutSeconds is >= 10 and <= 180 &&
        o.InventoryMaxAgeMinutes is >= 1 and <= 1440 && o.ProposalLifetimeMinutes is >= 1 and <= 60 &&
        o.AttachmentLifetimeHours is >= 1 and <= 168 && o.ReasoningEffort is "none" or "low" or "medium", "Invalid Shopping settings.")
    .ValidateOnStart();
builder.Services.PostConfigure<AgentOptions>(o => o.Definitions = o.Definitions.Where(a => a.Id != ProductConsultant.Id).Append(ProductConsultant.Definition).ToArray());
foreach (var toolName in ProductConsultant.Definition.ToolNames)
{
    var name = toolName;
    builder.Services.AddScoped<IAgentTool>(p => new ProductAgentTool(name, p.GetRequiredService<ProductLookup>(),
        p.GetRequiredService<BasketService>(), p.GetRequiredService<AttachmentService>(), p.GetRequiredService<MemoryMunchersDbContext>(),
        p.GetRequiredService<Microsoft.Extensions.Options.IOptions<ShoppingOptions>>(), p.GetRequiredService<TimeProvider>()));
}
builder.Services.AddProblemDetails();
builder.Services.AddExceptionHandler<AgentExceptionHandler>();

// Add services to the container.

builder.Services.AddControllers();
builder.Services.AddCors(options =>
{
    // PoC: allow every origin, header and method; no credentials are used.
    options.AddPolicy("PoCDevelopment", policy => policy.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod());
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

app.UseCors("PoCDevelopment");

app.MapControllers();

app.Run();
