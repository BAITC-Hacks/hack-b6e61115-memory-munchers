using MemoryMunchers.Agents;
using MemoryMunchers.Persistence;
using MemoryMunchers.Products;
using Microsoft.EntityFrameworkCore;
using MemoryMunchers.Shopping;
using System.Threading.RateLimiting;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddDbContext<MemoryMunchersDbContext>(options =>
    options.UseNpgsql(builder.Configuration.GetConnectionString("MemoryMunchers")));
builder.Services.AddAgenticWorkflow(builder.Configuration);
builder.Services.AddScoped<ProductCatalogReader>();
builder.Services.AddScoped<ProductCatalogService>();
builder.Services.AddScoped<ShopperContext>();
builder.Services.AddDataProtection();
builder.Services.AddSingleton<ShopperTokenService>();
builder.Services.AddScoped<ShopperDataService>();
builder.Services.AddScoped<MerchantFeedImporter>();
builder.Services.AddScoped<ProductLookup>();
builder.Services.AddScoped<BasketService>();
builder.Services.AddScoped<AttachmentService>();
builder.Services.AddScoped<ProductChatService>();
builder.Services.AddScoped<AgentEventSink>();
builder.Services.AddHostedService<DataRetentionCleanup>();
if (builder.Configuration.GetValue<bool>("DemoData:Enabled")) builder.Services.AddHostedService<DemoFeedService>();
builder.Services.AddOptions<ShoppingOptions>().BindConfiguration("Shopping")
    .Validate(o => o.MaxOutputTokens is >= 128 and <= 16384 && o.RunTimeoutSeconds is >= 10 and <= 180 &&
        o.InventoryMaxAgeMinutes is >= 1 and <= 1440 && o.ProposalLifetimeMinutes is >= 1 and <= 60 &&
        o.AttachmentLifetimeHours is >= 1 and <= 168 && o.SessionRetentionDays is >= 1 and <= 365 && o.ReasoningEffort is "none" or "low" or "medium", "Invalid Shopping settings.")
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
// Only the storefront origins may call the API from a browser. No cookies are used: the shopper token is a custom
// header, which also forces a CORS preflight, so other sites cannot silently change a basket.
var allowedOrigins = builder.Configuration.GetSection("Cors:AllowedOrigins").Get<string[]>() ?? [];
builder.Services.AddCors(options => options.AddPolicy("Storefront", policy => policy.WithOrigins(allowedOrigins)
    .WithHeaders("Content-Type", "Accept", ShopperTokenService.HeaderName).WithMethods("GET", "POST", "PATCH", "DELETE")));
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    static string Partition(HttpContext context) =>
        context.Request.Headers[ShopperTokenService.HeaderName].FirstOrDefault() is { Length: > 0 } token
            ? "s:" + token : "ip:" + context.Connection.RemoteIpAddress;
    void Policy(string name, int permits) => options.AddPolicy(name, context => RateLimitPartition.GetFixedWindowLimiter(
        Partition(context), _ => new FixedWindowRateLimiterOptions { PermitLimit = permits, Window = TimeSpan.FromMinutes(1) }));
    Policy("chat", builder.Configuration.GetValue("RateLimits:ChatPerMinute", 20));
    Policy("upload", builder.Configuration.GetValue("RateLimits:UploadsPerMinute", 10));
    Policy("basket", builder.Configuration.GetValue("RateLimits:BasketPerMinute", 60));
    // Token issuing is keyed by IP only, so a client cannot mint unlimited identities.
    options.AddPolicy("shopper", context => RateLimitPartition.GetFixedWindowLimiter("ip:" + context.Connection.RemoteIpAddress,
        _ => new FixedWindowRateLimiterOptions { PermitLimit = builder.Configuration.GetValue("RateLimits:ShoppersPerMinute", 10), Window = TimeSpan.FromMinutes(1) }));
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

app.UseCors("Storefront");
app.UseRateLimiter();
app.UseMiddleware<ShopperIdentityMiddleware>();

app.MapControllers();

app.Run();

public partial class Program;
