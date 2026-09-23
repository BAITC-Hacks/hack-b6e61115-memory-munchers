namespace MemoryMunchers.Shopping;

// PoC: no shopper identity; every client shares one shopper, basket and set of sessions.
public sealed class ShopperContext
{
    public static readonly Guid DefaultId = new("00000000-0000-0000-0000-000000000001");

    public Guid Id { get; set; } = DefaultId;
}
