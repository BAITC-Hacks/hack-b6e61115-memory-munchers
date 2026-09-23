using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace MemoryMunchers.Migrations
{
    /// <inheritdoc />
    public partial class AddProducts : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "Products",
                columns: table => new
                {
                    Id = table.Column<int>(type: "integer", nullable: false),
                    Code = table.Column<string>(type: "text", nullable: false),
                    SupplierArticle = table.Column<string>(type: "text", nullable: false),
                    Name = table.Column<string>(type: "text", nullable: false),
                    Brand = table.Column<string>(type: "text", nullable: false),
                    Category = table.Column<string>(type: "text", nullable: false),
                    CategoryPath = table.Column<string>(type: "text", nullable: false),
                    AllCategories = table.Column<string>(type: "text", nullable: false),
                    WebsitePrice = table.Column<decimal>(type: "numeric", nullable: true),
                    StorePrice = table.Column<decimal>(type: "numeric", nullable: true),
                    Currency = table.Column<string>(type: "text", nullable: false),
                    Availability = table.Column<string>(type: "text", nullable: false),
                    Unit = table.Column<string>(type: "text", nullable: false),
                    MinimumOrder = table.Column<decimal>(type: "numeric", nullable: true),
                    OrderMultiple = table.Column<decimal>(type: "numeric", nullable: true),
                    WebsiteOrderLimit = table.Column<decimal>(type: "numeric", nullable: true),
                    Description = table.Column<string>(type: "text", nullable: false),
                    SpecificationsJson = table.Column<string>(type: "jsonb", nullable: false),
                    ImageUrls = table.Column<string>(type: "text", nullable: false),
                    DocumentUrls = table.Column<string>(type: "text", nullable: false),
                    ProductUrl = table.Column<string>(type: "text", nullable: false),
                    SourceUrls = table.Column<string>(type: "text", nullable: false),
                    RetrievedAtUtc = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_Products", x => x.Id);
                });
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "Products");
        }
    }
}
