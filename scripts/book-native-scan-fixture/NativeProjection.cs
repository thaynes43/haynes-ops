using System.Collections;
using System.Globalization;
using System.Reflection;
using System.Reflection.Emit;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;

namespace NativeScannerFixture;

sealed record ProjectionProof(int ScalarCount, string ScalarSha256, string CoverSha256, string NativeCoverDirectory, string NativeSettingsSha256, int PendingEntityCount);

// Purpose-only projection for the five reviewed Ransom rows. No database writer.
static class NativeProjection
{
    static object Property(object value, string name) => value.GetType().GetProperty(name)?.GetValue(value)
        ?? throw new InvalidOperationException("native projection property missing");
    static object Invoke(Type type, object? target, string name, params object?[] args)
    {
        var method = type.GetMethods().Single(m => m.Name == name && m.GetParameters().Length == args.Length);
        return method.Invoke(target, args) ?? throw new InvalidOperationException("native projection returned no value");
    }
    static async Task<object> Result(object task, DateTimeOffset end)
    {
        await NativeBindings.Await(task, end);
        return Property(task, "Result");
    }
    public static void RequireNoPending(IServiceProvider services)
    {
        var context = services.GetRequiredService(NativeBindings.Type("Kavita.Database", "Kavita.Database.DataContext"));
        var tracker = Property(context, "ChangeTracker");
        tracker.GetType().GetMethod("DetectChanges", Type.EmptyTypes)!.Invoke(tracker, null);
        var entries = (IEnumerable)tracker.GetType().GetMethods().Single(m => m.Name == "Entries" && !m.IsGenericMethod && m.GetParameters().Length == 0).Invoke(tracker, null)!;
        FixtureProtocol.Require(!entries.Cast<object>().Any(e => Property(e, "State").ToString() is "Added" or "Modified" or "Deleted"), "native projection left pending entity changes");
        // Never Clear, AcceptAllChanges, save or discard a scope to hide pending work.
    }
    public static async Task<ProjectionProof> Derive(IServiceProvider services, FixturePacket packet, DatabaseSnapshot before)
    {
        NativeProof.RequirePrivateUmask();
        var bookType = NativeBindings.Type("Kavita.API", "Kavita.API.Services.IBookService");
        var book = services.GetRequiredService(bookType);
        FixtureProtocol.Require(book.GetType().FullName == "Kavita.Services.BookService", "native book implementation differs");
        var image = services.GetRequiredService(NativeBindings.Type("Kavita.API", "Kavita.API.Services.IImageService"));
        FixtureProtocol.Require(image.GetType().FullName == "Kavita.Services.ImageService"
            && book.GetType().GetFields(BindingFlags.NonPublic | BindingFlags.Instance).Count(f => ReferenceEquals(f.GetValue(book), image)) == 1, "native cover encoder binding differs");
        var comic = Invoke(bookType, book, "GetComicInfo", packet.TargetFilePath);
        var direct = Invoke(bookType, book, "ParseInfo", packet.TargetFilePath);
        var basic = ActivatorUtilities.CreateInstance(services, NativeBindings.Type("Kavita.Services", "Kavita.Services.Scanner.BasicParser"));
        var parserType = NativeBindings.Type("Kavita.Services", "Kavita.Services.Scanner.BookParser");
        var parser = ActivatorUtilities.CreateInstance(services, parserType, basic);
        var libraryType = NativeBindings.Type("Kavita.Models", "Kavita.Models.Entities.Enums.LibraryType");
        var parsed = Invoke(parserType, parser, "Parse", packet.TargetFilePath, Path.GetDirectoryName(packet.TargetFilePath), "/data/cephfs-hdd/data/media/books/EBooks", Enum.Parse(libraryType, "Book"), true, comic);
        var scannerParser = NativeBindings.Type("Kavita.Services", "Kavita.Services.Scanner.Parser");
        FixtureProtocol.Require((bool)Invoke(scannerParser, null, "IsLooseLeafVolume", Property(parsed, "Volumes"))
            && (bool)Invoke(scannerParser, null, "IsDefaultChapter", Property(parsed, "Chapters"))
            && Property(parsed, "Series").Equals("Ransom") && Property(parsed, "Title").Equals("Ransom")
            && Property(direct, "Series").Equals("Ransom") && Property(direct, "Title").Equals("Ransom")
            && Property(parsed, "Format").ToString() == "Epub", "native loose-leaf projection differs");
        var countType = NativeBindings.Type("Kavita.Services", "Kavita.Services.Helpers.ParsedCountHelper");
        var count = (int)Invoke(countType, null, "GetCalculatedCount", parsed);
        var total = (int)Invoke(countType, null, "GetTotalCount", parsed);
        var chapterType = NativeBindings.Type("Kavita.Models", "Kavita.Models.Entities.Chapter");
        var chapter = Activator.CreateInstance(chapterType)!;
        var chapterHelpers = NativeBindings.Type("Kavita.Services", "Kavita.Services.Extensions.ChapterExtensions");
        chapterHelpers.GetMethod("UpdateFrom")!.Invoke(null, [chapter, parsed]);
        foreach (var (target, source) in new[] { ("MinNumber", "LowestChapter"), ("MaxNumber", "HighestChapter") })
            chapterType.GetProperty(target)!.SetValue(chapter, Property(parsed, source));
        var range = (string)Invoke(chapterHelpers, null, "GetNumberTitle", chapter);
        FixtureProtocol.Require(count == 0 && total == 1 && (bool)Property(chapter, "IsSpecial")
            && Property(chapter, "Title").Equals("Ransom") && range == "Ransom", "native chapter projection differs");
        FixtureProtocol.Require(NativeProof.Row(before, "Series", packet.Target.Series)["SortNameLocked"] == "int:0"
            && NativeProof.Row(before, "SeriesMetadata", packet.Target.Series)["PublicationStatusLocked"] == "int:0"
            && NativeProof.Row(before, "Volume", packet.Target.Volume)["CoverImageLocked"] == "int:0"
            && NativeProof.Row(before, "Chapter", packet.Target.Chapter)["CoverImageLocked"] == "int:0", "native projection lock differs");
        var sort = (string)Property(parsed, "Series");
        if (NativeProof.Row(before, "Library", packet.Target.Library)["RemovePrefixForSortName"] == "int:1")
            sort = (string)SortTitleMethod().Invoke(null, [sort])!;
        var seriesSort = (string)Property(parsed, "SeriesSort");
        if (!string.IsNullOrEmpty(seriesSort)) sort = seriesSort;
        FixtureProtocol.Require(sort == "Ransom", "native unlocked sort projection differs");
        var seriesType = NativeBindings.Type("Kavita.Models", "Kavita.Models.Entities.Series");
        var metadataType = NativeBindings.Type("Kavita.Models", "Kavita.Models.Entities.Metadata.SeriesMetadata");
        var series = Activator.CreateInstance(seriesType)!;
        var metadata = Activator.CreateInstance(metadataType)!;
        seriesType.GetProperty("Metadata")!.SetValue(series, metadata);
        seriesType.GetProperty("Format")!.SetValue(series, Property(parsed, "Format"));
        var volumeType = NativeBindings.Type("Kavita.Models", "Kavita.Models.Entities.Volume");
        seriesType.GetProperty("Volumes")!.SetValue(series, Activator.CreateInstance(typeof(List<>).MakeGenericType(volumeType)));
        chapterType.GetProperty("Count")!.SetValue(chapter, count);
        chapterType.GetProperty("TotalCount")!.SetValue(chapter, total);
        var chapters = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(chapterType))!;
        chapters.Add(chapter);
        var processType = NativeBindings.Type("Kavita.Services", "Kavita.Services.Scanner.ProcessSeries");
        var process = ActivatorUtilities.CreateInstance(services, processType);
        processType.GetMethod("DeterminePublicationStatus", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(process, [series, chapters]);
        FixtureProtocol.Require((int)Property(metadata, "MaxCount") == 1 && (int)Property(metadata, "TotalCount") == 1
            && Property(metadata, "PublicationStatus").ToString() == "Completed" && Convert.ToInt32(Property(metadata, "PublicationStatus"), CultureInfo.InvariantCulture) == 2,
            "native detached single-book publication projection differs");
        var koreader = (string)Invoke(NativeBindings.Type("Kavita.Services", "Kavita.Services.Helpers.KoreaderHelper"), null, "HashContents", packet.TargetFilePath);
        var unitType = NativeBindings.Type("Kavita.API", "Kavita.API.Database.IUnitOfWork");
        var unit = services.GetRequiredService(unitType);
        FixtureProtocol.Require(ReferenceEquals(Property(unit, "DataContext"), services.GetRequiredService(NativeBindings.Type("Kavita.Database", "Kavita.Database.DataContext"))), "helper unit of work uses a different native scoped context");
        FixtureProtocol.Require(book.GetType().GetFields(BindingFlags.NonPublic | BindingFlags.Instance).Count(f => ReferenceEquals(f.GetValue(book), unit)) == 1
            && process.GetType().GetFields(BindingFlags.NonPublic | BindingFlags.Instance).Count(f => ReferenceEquals(f.GetValue(process), unit)) == 1, "helper unit-of-work capture differs");
        var settingsRepository = Property(unit, "SettingsRepository");
        var settings = await Result(Invoke(settingsRepository.GetType(), settingsRepository, "GetSettingsDtoAsync", CancellationToken.None), packet.ExpiresAt);
        var encode = Property(settings, "EncodeMediaAs");
        var size = Property(settings, "CoverImageSize");
        var coverDirectory = "/tmp/ransom-cover-derivation-" + packet.Phase;
        FixtureProtocol.Require(!Directory.Exists(coverDirectory), "cover derivation directory already exists");
        Directory.CreateDirectory(coverDirectory);
        File.SetUnixFileMode(coverDirectory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        // The pinned native helper returns a basename, not the output's full path.
        var coverName = (string)Invoke(bookType, book, "GetCoverImage", packet.TargetFilePath, "projection", coverDirectory, encode, size);
        var cover = CoverPath(coverDirectory, coverName);
        NativeProof.PrivateFile(cover, 8 * 1024 * 1024);
        var values = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            ["Series:SortName"] = FixtureProtocol.Cell(sort), ["Chapter:Count"] = FixtureProtocol.Cell(count),
            ["Chapter:IsSpecial"] = "int:1", ["Chapter:Range"] = FixtureProtocol.Cell(range),
            ["Chapter:Title"] = FixtureProtocol.Cell(Property(chapter, "Title")), ["Chapter:TotalCount"] = FixtureProtocol.Cell(total),
            ["MangaFile:Bytes"] = FixtureProtocol.Cell(new FileInfo(packet.TargetFilePath).Length), ["MangaFile:KoreaderHash"] = FixtureProtocol.Cell(koreader),
            ["SeriesMetadata:TotalCount"] = FixtureProtocol.Cell(Property(metadata, "TotalCount")), ["SeriesMetadata:PublicationStatus"] = FixtureProtocol.Cell(Convert.ToInt32(Property(metadata, "PublicationStatus"), CultureInfo.InvariantCulture)),
            ["SeriesMetadata:RowVersion"] = NextRowVersion(NativeProof.Row(before, "SeriesMetadata", packet.Target.Series)["RowVersion"])
        };
        FixtureProtocol.Require(values["MangaFile:Bytes"] == "int:1081344", "pinned candidate byte count differs");
        RequireValues(packet, values);
        RequireNoPending(services);
        // Only hashes/counts may escape; native values and cover bytes stay private.
        var directoryType = NativeBindings.Type("Kavita.API", "Kavita.API.Services.IDirectoryService");
        var nativeCoverDirectory = Path.GetFullPath((string)Property(services.GetRequiredService(directoryType), "CoverImageDirectory"));
        FixtureProtocol.Require(nativeCoverDirectory == "/kavita/config/covers", "native cover directory differs from tagged private binding");
        return new(values.Count, FixtureProtocol.HashText(JsonSerializer.Serialize(values)), FixtureProtocol.Sha(File.ReadAllBytes(cover)),
            nativeCoverDirectory, FixtureProtocol.HashText(encode + ":" + size), 0);
    }
    public static string NextRowVersion(string cell)
    {
        FixtureProtocol.Require(cell.StartsWith("int:", StringComparison.Ordinal)
            && uint.TryParse(cell[4..], NumberStyles.None, CultureInfo.InvariantCulture, out var original)
            && original <= uint.MaxValue - 2, "native uint token overflows or differs");
        return "int:" + (uint.Parse(cell[4..], CultureInfo.InvariantCulture) + 2).ToString(CultureInfo.InvariantCulture);
    }
    internal static string CoverPath(string directory, string name)
    {
        FixtureProtocol.Require(Path.IsPathFullyQualified(directory) && name.Length is > 0 and <= 128
            && name == Path.GetFileName(name) && name.StartsWith("projection.", StringComparison.Ordinal)
            && !name.Contains("..", StringComparison.Ordinal), "native cover basename differs");
        var path = Path.GetFullPath(Path.Combine(directory, name));
        FixtureProtocol.Require(Path.GetDirectoryName(path) == directory, "native cover is outside the separate private directory");
        return path;
    }
    public static void RequireValues(FixturePacket packet, IReadOnlyDictionary<string, string> values)
    {
        FixtureProtocol.Require(values.Count == 11 && packet.ScanAllowances.Count(r => !r.ScanClock && !r.NativeColor) == values.Count, "native scalar derivation scope differs");
        foreach (var rule in packet.ScanAllowances.Where(r => !r.ScanClock && !r.NativeColor))
            FixtureProtocol.Require(values.TryGetValue(rule.Table + ":" + rule.Field, out var derived) && derived == rule.After, "approved scalar differs from independent native derivation");
    }
    public static void RequireAfterCover(DatabaseSnapshot before, DatabaseSnapshot after, FixturePacket packet, ProjectionProof proof)
    {
        var volume = NativeProof.Row(after, "Volume", packet.Target.Volume);
        FixtureProtocol.Require(volume["CoverImage"] == NativeProof.Row(before, "Volume", packet.Target.Volume)["CoverImage"]
            && volume["CoverImage"] == NativeProof.Row(after, "Chapter", packet.Target.Chapter)["CoverImage"], "native volume cover binding differs");
        RequireCoverBytes(proof.NativeCoverDirectory, volume["CoverImage"], proof.CoverSha256);
    }
    internal static void RequireCoverBytes(string directory, string nativeCell, string expectedSha256)
    {
        FixtureProtocol.Require(nativeCell.StartsWith("text:", StringComparison.Ordinal) && FixtureProtocol.IsHash(expectedSha256), "native cover cell/hash differs");
        var name = nativeCell[5..];
        FixtureProtocol.Require(name.Length is > 0 and <= 255 && name == Path.GetFileName(name) && !name.Contains("..", StringComparison.Ordinal), "native cover file is outside its exact directory");
        var path = Path.Combine(directory, name);
        NativeProof.PrivateFile(path, 8 * 1024 * 1024);
        FixtureProtocol.Require(FixtureProtocol.Sha(File.ReadAllBytes(path)) == expectedSha256, "actual native volume cover differs from independently encoded candidate");
    }

    sealed record Instruction(OpCode Op, int? Token, int? Integer);
    static Instruction[] Instructions(MethodInfo method)
    {
        var bytes = method.GetMethodBody()?.GetILAsByteArray() ?? throw new InvalidOperationException("native IL missing");
        var codes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static).Where(f => f.FieldType == typeof(OpCode)).Select(f => (OpCode)f.GetValue(null)!).ToDictionary(o => unchecked((ushort)o.Value));
        var result = new List<Instruction>();
        for (var at = 0; at < bytes.Length;)
        {
            var code = (ushort)bytes[at++];
            if (code == 0xfe) code = (ushort)(0xfe00 | bytes[at++]);
            FixtureProtocol.Require(codes.TryGetValue(code, out var op), "unknown native IL opcode");
            var start = at;
            var length = op.OperandType switch
            {
                OperandType.InlineNone => 0,
                OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                OperandType.InlineVar => 2,
                OperandType.InlineI8 or OperandType.InlineR => 8,
                OperandType.InlineSwitch => checked(4 + 4 * BitConverter.ToInt32(bytes, at)),
                _ => 4
            };
            at = checked(at + length);
            FixtureProtocol.Require(at <= bytes.Length, "truncated native IL");
            result.Add(new(op, op.OperandType == OperandType.InlineMethod ? BitConverter.ToInt32(bytes, start) : null,
                op == OpCodes.Ldc_I4_1 ? 1 : op == OpCodes.Ldc_I4 ? BitConverter.ToInt32(bytes, start) : null));
        }
        return result.ToArray();
    }
    static MethodBase Resolve(MethodInfo source, Instruction instruction) => source.Module.ResolveMethod(instruction.Token!.Value, source.DeclaringType?.GetGenericArguments(), source.GetGenericArguments())!;
    public static void RequireVirtualDispatch(MethodInfo cancellation, MethodInfo boolean)
    {
        var instructions = Instructions(cancellation);
        var calls = instructions.Where(i => i.Token.HasValue).ToArray();
        FixtureProtocol.Require(calls.Length == 1 && calls[0].Op == OpCodes.Callvirt && Resolve(cancellation, calls[0]) == boolean
            && instructions.Count(i => i.Integer == 1) == 1 && boolean.IsVirtual, "actual EF cancellation overload does not dispatch virtually to bool overload");
    }
    static MethodInfo SortTitleMethod()
    {
        var type = NativeBindings.Type("Kavita.Services", "Kavita.Services.Helpers.BookSortTitlePrefixHelper");
        var method = type.GetMethod("GetSortTitle", [typeof(string)]);
        FixtureProtocol.Require(method is not null && method.IsPublic && method.IsStatic && method.ReturnType == typeof(string), "native string sort helper signature differs");
        return method!;
    }
    public static void InspectSaveHooks()
    {
        FixtureProtocol.Require(CoverPath("/tmp/synthetic-cover", "projection.jpg") == "/tmp/synthetic-cover/projection.jpg", "native cover basename was not joined to its directory");
        foreach (var name in new[] { "/tmp/projection.jpg", "../projection.jpg", "another.jpg" })
        {
            var pathRefused = false;
            try { CoverPath("/tmp/synthetic-cover", name); } catch (InvalidOperationException) { pathRefused = true; }
            FixtureProtocol.Require(pathRefused, "unsafe native cover basename accepted");
        }
        foreach (var color in new[] { "null:", "text:#000000", "text:#A1B2C3", "text:#FFFFFF" }) FixtureProtocol.RequireNativeColor(color);
        foreach (var color in new[] { "text:#abcdef", "text:#12345", "text:#1234567", "text:#GG0000", "text: #112233", "int:123456", "blob:AQI=" })
        {
            var colorRefused = false;
            try { FixtureProtocol.RequireNativeColor(color); } catch (InvalidOperationException) { colorRefused = true; }
            FixtureProtocol.Require(colorRefused, "malformed native color was accepted");
        }
        var coverControl = Path.Combine(Path.GetTempPath(), "ransom-cover-control-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(coverControl);
        try
        {
            var path = Path.Combine(coverControl, "projection.jpg");
            File.WriteAllBytes(path, [1, 2, 3]); File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
            RequireCoverBytes(coverControl, "text:projection.jpg", FixtureProtocol.Sha([1, 2, 3]));
            var wrongCoverRefused = false;
            try { RequireCoverBytes(coverControl, "text:projection.jpg", FixtureProtocol.Sha([3, 2, 1])); } catch (InvalidOperationException) { wrongCoverRefused = true; }
            FixtureProtocol.Require(wrongCoverRefused, "different native cover bytes were accepted");
        }
        finally { Directory.Delete(coverControl, recursive: true); }
        foreach (var (assembly, name, method, arguments) in new[] {
            ("Kavita.Services", "Kavita.Services.Extensions.ChapterExtensions", "UpdateFrom", 2),
            ("Kavita.Services", "Kavita.Services.Extensions.ChapterExtensions", "GetNumberTitle", 1),
            ("Kavita.Services", "Kavita.Services.Helpers.KoreaderHelper", "HashContents", 1),
            ("Kavita.Services", "Kavita.Services.Helpers.ParsedCountHelper", "GetCalculatedCount", 1),
            ("Kavita.Services", "Kavita.Services.Helpers.ParsedCountHelper", "GetTotalCount", 1),
            ("Kavita.Services", "Kavita.Services.ImageService", "CalculateColorScape", 1),
            ("Kavita.Services", "Kavita.Services.Scanner.BookParser", "Parse", 6) })
            FixtureProtocol.Require(NativeBindings.Type(assembly, name).GetMethods().Count(m => m.Name == method && m.GetParameters().Length == arguments) == 1, "native projection helper signature differs");
        var sortTitle = SortTitleMethod();
        foreach (var title in new[] { "The Ransom", "Ransom" })
            FixtureProtocol.Require((string)sortTitle.Invoke(null, [title])! == "Ransom", "native string sort helper control differs");
        FixtureProtocol.Require(NativeBindings.Type("Kavita.Services", "Kavita.Services.Scanner.ProcessSeries").GetMethod("DeterminePublicationStatus", BindingFlags.NonPublic | BindingFlags.Instance)?.GetParameters().Length == 2, "native publication helper signature differs");
        var image = NativeBindings.Type("Kavita.Services", "Kavita.Services.ImageService");
        var calculate = image.GetMethod("CalculateColorScape", [typeof(string)])!;
        var update = image.GetMethods().Single(m => m.Name == "UpdateColorScape" && m.GetParameters().Length == 1);
        var updateCalls = Instructions(update).Where(i => i.Token.HasValue).Select(i => Resolve(update, i)).ToArray();
        FixtureProtocol.Require(calculate.ReturnType.FullName == "Kavita.Models.DTOs.ColorScape" && calculate.IsStatic
            && update.ReturnType == typeof(void) && updateCalls.Count(m => m == calculate) == 1
            && updateCalls.Count(m => m.Name == "set_PrimaryColor") == 1 && updateCalls.Count(m => m.Name == "set_SecondaryColor") == 1,
            "compiled native colorscape assignment surface differs");
        var calculateCalls = Instructions(calculate).Where(i => i.Token.HasValue).Select(i => Resolve(calculate, i)).ToArray();
        FixtureProtocol.Require(calculateCalls.Count(m => m.DeclaringType == image && m.Name == "GetPrimarySecondaryColors") == 1
            && calculateCalls.Count(m => m.DeclaringType == image && m.Name == "RgbToHex") == 2, "compiled native color output surface differs");
        var ef = NativeBindings.Type("Microsoft.EntityFrameworkCore", "Microsoft.EntityFrameworkCore.DbContext");
        FixtureProtocol.Require(ef.Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.StartsWith("10.0.6", StringComparison.Ordinal) == true, "actual EF package is not 10.0.6");
        var cancellation = ef.GetMethod("SaveChangesAsync", [typeof(CancellationToken)])!;
        var boolean = ef.GetMethod("SaveChangesAsync", [typeof(bool), typeof(CancellationToken)])!;
        RequireVirtualDispatch(cancellation, boolean);
        var native = NativeBindings.Type("Kavita.Database", "Kavita.Database.DataContext");
        foreach (var parameters in new[] { new[] { typeof(CancellationToken) }, new[] { typeof(bool), typeof(CancellationToken) } })
        {
            var method = native.GetMethod("SaveChangesAsync", parameters)!;
            var calls = Instructions(method).Where(i => i.Token.HasValue).Select(i => Resolve(method, i)).ToArray();
            FixtureProtocol.Require(method.DeclaringType == native && method.GetBaseDefinition().DeclaringType == ef
                && calls.Length == 2 && calls[0].DeclaringType == native && calls[0].Name == "OnSaveChanges"
                && calls[1] == ef.GetMethod("SaveChangesAsync", parameters), "native async save hooks differ");
        }
        var metadata = NativeBindings.Type("Kavita.Models", "Kavita.Models.Entities.Metadata.SeriesMetadata");
        var hook = metadata.GetMethod("OnSavingChanges")!;
        var il = Instructions(hook);
        var property = metadata.GetProperty("RowVersion")!;
        FixtureProtocol.Require(property.PropertyType == typeof(uint) && il.Count(i => i.Integer == 1) == 1 && il.Count(i => i.Op == OpCodes.Add) == 1
            && il.Where(i => i.Token.HasValue).Select(i => Resolve(hook, i)).SequenceEqual(new MethodBase[] { property.GetMethod!, property.GetSetMethod(true)! }), "native metadata increment hook differs");
        var value = Activator.CreateInstance(metadata)!;
        property.GetSetMethod(true)!.Invoke(value, [100u]); hook.Invoke(value, null); hook.Invoke(value, null);
        FixtureProtocol.Require((uint)property.GetValue(value)! == 102u, "native uint token hook differs");
        var synthetic = typeof(DispatchControl);
        var boolControl = synthetic.GetMethod(nameof(DispatchControl.Save))!;
        RequireVirtualDispatch(synthetic.GetMethod(nameof(DispatchControl.Cancellation))!, boolControl);
        var refused = false;
        try { RequireVirtualDispatch(synthetic.GetMethod(nameof(DispatchControl.NonVirtual))!, boolControl); }
        catch (InvalidOperationException) { refused = true; }
        FixtureProtocol.Require(refused, "nonvirtual dispatch control was accepted");
        PendingControls(ef, native, metadata);
    }
    class DispatchControl
    {
        public virtual Task<int> Save(bool accept, CancellationToken token) => Task.FromResult(accept ? 1 : 0);
        public Task<int> Cancellation(CancellationToken token) => Save(true, token);
        public Task<int> NonVirtual(CancellationToken token) => Task.FromResult(1);
    }
    static void PendingControls(Type ef, Type native, Type metadata)
    {
        var builderType = NativeBindings.Type("Microsoft.EntityFrameworkCore", "Microsoft.EntityFrameworkCore.DbContextOptionsBuilder");
        var builder = Activator.CreateInstance(builderType)!;
        var sqlite = NativeBindings.Type("Microsoft.EntityFrameworkCore.Sqlite", "Microsoft.EntityFrameworkCore.SqliteDbContextOptionsBuilderExtensions");
        sqlite.GetMethods().Single(m => m.Name == "UseSqlite" && !m.IsGenericMethod && m.GetParameters().Length == 3
            && m.GetParameters()[0].ParameterType == builderType && m.GetParameters()[1].ParameterType == typeof(string)).Invoke(null, [builder, "Data Source=:memory:", null]);
        using var context = (IDisposable)Activator.CreateInstance(native, Property(builder, "Options"))!;
        using var services = new ServiceCollection().AddSingleton(native, context).BuildServiceProvider();
        RequireNoPending(services);
        var entity = Activator.CreateInstance(metadata)!;
        metadata.GetProperty("Id")!.SetValue(entity, FixtureProtocol.ReviewedTarget.Series);
        metadata.GetProperty("SeriesId")!.SetValue(entity, FixtureProtocol.ReviewedTarget.Series);
        var entry = ef.GetMethods().Single(m => m.Name == "Attach" && !m.IsGenericMethod && m.GetParameters().Length == 1).Invoke(context, [entity])!;
        var state = entry.GetType().GetProperty("State")!;
        foreach (var name in new[] { "Added", "Modified", "Deleted" })
        {
            state.SetValue(entry, Enum.Parse(state.PropertyType, name));
            var refused = false;
            try { RequireNoPending(services); } catch (InvalidOperationException) { refused = true; }
            FixtureProtocol.Require(refused && state.GetValue(entry)!.ToString() == name, "pending native entry accepted or hidden");
            state.SetValue(entry, Enum.Parse(state.PropertyType, "Unchanged"));
        }
        // DetectChanges must discover a deferred property mutation as well.
        metadata.GetProperty("Summary")!.SetValue(entity, "synthetic-deferred-change");
        var deferredRefused = false;
        try { RequireNoPending(services); } catch (InvalidOperationException) { deferredRefused = true; }
        FixtureProtocol.Require(deferredRefused && state.GetValue(entry)!.ToString() == "Modified", "deferred native mutation escaped DetectChanges");
        // No connection is opened, no host/migration is run, and no entity is saved.
    }
}
