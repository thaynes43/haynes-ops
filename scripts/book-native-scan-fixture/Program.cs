using System.Collections;
using System.Data.Common;
using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;
using System.IO.Compression;
using System.Runtime.InteropServices;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using NativeScannerFixture;

var output = Console.Out;
string stage = "admission";
FixturePacket? packet = null;
var exitCode = 2;
string? resultJson = null;
Timer? alarm = null;
try
{
    if (args.SequenceEqual(new[] { "--self-test-deadline-child" }))
    {
        using var deadline = ProcessDeadline.Arm(DateTimeOffset.UtcNow.AddMilliseconds(250));
        // One finite write fills an undrained pipe; no polling or CPU load loop.
        Console.Out.Write(new string('x', 4 * 1024 * 1024));
        await Task.Delay(TimeSpan.FromSeconds(3));
        return 99;
    }
    if (args.SequenceEqual(new[] { "--self-test-deadline" }))
    {
        using var child = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(Environment.ProcessPath!, "--self-test-deadline-child") { RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false })!;
        try { await child.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(3)); }
        catch { child.Kill(entireProcessTree: true); throw; }
        FixtureProtocol.Require(child.ExitCode == 124, "original deadline depended on stdout drainage");
        output.WriteLine("PASS original deadline hard-exits with full undrained stdout; no native host/scan/input");
        return 0;
    }
    AssemblyLoadContext.Default.Resolving += (_, name) =>
    {
        var path = Path.Combine("/kavita", name.Name + ".dll");
        return File.Exists(path) ? AssemblyLoadContext.Default.LoadFromAssemblyPath(path) : null;
    };
    if (args.SequenceEqual(new[] { "--self-test" }))
    {
        FixtureProtocol.SelfTest();
        NativeProof.GenericSelfTest();
        NativeBindings.Inspect();
        output.WriteLine($"PASS 11 packet refusals, 3 durable-ACK refusals, 6 live-native drift refusals, typed cells, saved-state barriers, inverse and actual native reflection signatures; runtime={RuntimeInformation.FrameworkDescription}; timezone={TimeZoneInfo.Local.Id}; offset={TimeZoneInfo.Local.GetUtcOffset(DateTimeOffset.UtcNow)}; no native host/scan started");
        return 0;
    }
    FixtureProtocol.Require(args.SequenceEqual(new[] { "--prepared-private-fixture" }), "unknown entrypoint");
    FixtureProtocol.Require(!File.Exists("/var/run/secrets/kubernetes.io/serviceaccount/token"), "service-account token is mounted");
    foreach (var mount in new[] { "/fixture-input", "/kavita/config", "/data/cephfs-hdd/data/media/books/EBooks", "/tmp" })
        NativeProof.RequirePrivateTmpfs(mount);
    var packetPath = "/fixture-input/approved-packet.json";
    await NativeProof.WaitForPacket(packetPath, TimeSpan.FromSeconds(15));
    NativeProof.PrivateFile(packetPath, 1024 * 1024);
    packet = JsonSerializer.Deserialize<FixturePacket>(await File.ReadAllBytesAsync(packetPath)) ?? throw new InvalidOperationException("private packet missing");
    FixtureProtocol.Validate(packet, DateTimeOffset.UtcNow);
    FixtureProtocol.Require(Environment.GetEnvironmentVariable("POD_UID") == packet.PodUid && Environment.GetEnvironmentVariable("NODE_NAME") == packet.Node, "native Pod/node differs");
    FixtureProtocol.Require(FixtureProtocol.Sha(await File.ReadAllBytesAsync(typeof(FixtureProtocol).Assembly.Location)) == packet.HarnessSha256, "reviewed harness differs");
    FixtureProtocol.Require(FixtureProtocol.Sha(await File.ReadAllBytesAsync(FixtureProtocol.NativeAssembly)) == packet.NativeAssemblySha256, "published native assembly differs");
    alarm = ProcessDeadline.Arm(packet.ExpiresAt);
    foreach (var pin in packet.Inputs)
    {
        FixtureProtocol.Require(!pin.Path.Contains("..") && (pin.Path.StartsWith("/fixture-input/", StringComparison.Ordinal) || pin.Path == packet.CandidateDbPath || pin.Path == packet.TargetFilePath || pin.Path == "/kavita/config/appsettings.json"), "private pin is outside input scope");
        NativeProof.PrivateFile(pin.Path, 32 * 1024 * 1024);
        FixtureProtocol.Require(FixtureProtocol.Sha(await File.ReadAllBytesAsync(pin.Path)) == pin.Sha256, "private bytes changed");
    }
    NativeBindings.Inspect();
    using (var source = JsonDocument.Parse(await File.ReadAllBytesAsync("/fixture-input/source-proof.json")))
    {
        var live = source.RootElement.GetProperty("LiveNative").Deserialize<LiveNativeProof>() ?? throw new InvalidOperationException("fresh live native proof missing");
        FixtureProtocol.ValidateLiveNative(packet, live, DateTimeOffset.UtcNow);
        foreach (var module in live.Modules)
            FixtureProtocol.Require(FixtureProtocol.Sha(await File.ReadAllBytesAsync(module.Path)) == module.Sha256, "actual deployed native module differs from fixture; reviewed successor required");
    }
    NativeProof.RequireEpubMemberDelta(packet);
    var original = NativeProof.ReadDatabase(packet.OriginalDbPath);
    var before = NativeProof.ReadDatabase(packet.CandidateDbPath);
    FixtureProtocol.Require(before.SchemaSha256 == packet.ExpectedSchemaSha256 && original.SchemaSha256 == before.SchemaSha256, "native schema changed");
    NativeProof.RequireDelta(original, before, packet.CatalogDelta);
    NativeProof.RequireTarget(before, packet);
    NativeProof.SavePrivate("/kavita/config/proof-before.json", before);
    stage = "native-build";
    // Suppress native console logs; private vendor log files remain in tmpfs.
    Console.SetOut(TextWriter.Null);
    Console.SetError(TextWriter.Null);
    var started = DateTimeOffset.UtcNow;
    Directory.SetCurrentDirectory("/kavita");
    var program = Assembly.LoadFrom(FixtureProtocol.NativeAssembly).GetType("Kavita.Server.Program", true)!;
    var create = program.GetMethod("CreateHostBuilder", BindingFlags.NonPublic | BindingFlags.Static) ?? throw new MissingMethodException();
    var builder = (IHostBuilder)(create.Invoke(null, new object[] { Array.Empty<string>() }) ?? throw new InvalidOperationException("native builder missing"));
    // Build only. Main/Start/Run and all hosted services are never invoked.
    using var host = builder.Build();
    var afterBuild = NativeProof.ReadDatabase(packet.CandidateDbPath);
    NativeProof.SavePrivate("/kavita/config/proof-after-build.json", afterBuild);
    NativeProof.RequireDelta(before, afterBuild, []);
    var lifetime = host.Services.GetRequiredService<IHostApplicationLifetime>();
    FixtureProtocol.Require(!lifetime.ApplicationStarted.IsCancellationRequested, "native host was started");
    object? nativeDbBinding = null;
    using (var scope = host.Services.CreateScope())
    {
        nativeDbBinding = NativeBindings.ProvePrivateDbContext(scope.ServiceProvider, packet.CandidateDbPath);
        var scannerType = NativeBindings.Type("Kavita.API", "Kavita.API.Services.Scanner.IScannerService");
        var scanner = scope.ServiceProvider.GetRequiredService(scannerType);
        FixtureProtocol.Require(scanner.GetType().FullName == "Kavita.Services.Scanner.ScannerService", "scanner is not the published native implementation");
        // Initialize the native in-memory job store without starting its server.
        var storage = NativeBindings.Type("Hangfire.Core", "Hangfire.JobStorage");
        _ = scope.ServiceProvider.GetRequiredService(storage);
        var afterBinding = NativeProof.ReadDatabase(packet.CandidateDbPath);
        NativeProof.SavePrivate("/kavita/config/proof-after-bind.json", afterBinding);
        NativeProof.RequireDelta(before, afterBinding, []);
        stage = "native-scan";
        var scan = scannerType.GetMethod("ScanSeries", new[] { typeof(int), typeof(bool) }) ?? throw new MissingMethodException();
        await NativeBindings.Await(scan.Invoke(scanner, new object[] { packet.Target.Series, true }), packet.ExpiresAt);
    }
    stage = "native-cleanup-predicate";
    using (var scope = host.Services.CreateScope())
    {
        _ = NativeBindings.ProvePrivateDbContext(scope.ServiceProvider, packet.CandidateDbPath);
        var unitType = NativeBindings.Type("Kavita.API", "Kavita.API.Database.IUnitOfWork");
        var unit = scope.ServiceProvider.GetRequiredService(unitType);
        var repo = unitType.GetProperty("SeriesRepository")!.GetValue(unit) ?? throw new InvalidOperationException("native repository missing");
        var parsedType = NativeBindings.Type("Kavita.Models", "Kavita.Models.Parser.ParsedSeries");
        var retained = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(parsedType))!;
        foreach (var key in packet.RetainedParsedKeys)
        {
            var value = Activator.CreateInstance(parsedType)!;
            parsedType.GetProperty("Name")!.SetValue(value, key.Name);
            parsedType.GetProperty("NormalizedName")!.SetValue(value, key.NormalizedName);
            var format = parsedType.GetProperty("Format")!;
            format.SetValue(value, Enum.ToObject(format.PropertyType, key.Format));
            retained.Add(value);
        }
        var cleanup = repo.GetType().GetMethod("RemoveSeriesNotInListAsync") ?? throw new MissingMethodException();
        var task = (Task)(cleanup.Invoke(repo, new object[] { retained, packet.Target.Library, CancellationToken.None }) ?? throw new InvalidOperationException("native cleanup missing"));
        await task.WaitAsync(packet.ExpiresAt - DateTimeOffset.UtcNow);
        var removed = (IEnumerable)(task.GetType().GetProperty("Result")!.GetValue(task) ?? throw new InvalidOperationException("native cleanup result missing"));
        FixtureProtocol.Require(!removed.Cast<object>().Any(), "actual cleanup would remove a retained work");
        // Never commit cleanup removals or run a partial-corpus library scan.
    }
    FixtureProtocol.Require(!lifetime.ApplicationStarted.IsCancellationRequested, "native hosted workers were started");
    host.Dispose();
    var finished = DateTimeOffset.UtcNow;
    stage = "state-readback";
    var after = NativeProof.ReadDatabase(packet.CandidateDbPath);
    NativeProof.SavePrivate("/kavita/config/proof-after.json", after);
    NativeProof.RequireTarget(after, packet);
    NativeProof.RequireScanDelta(before, after, packet, started, finished);
    stage = "private-inverse";
    NativeProof.InvertCatalog(packet);
    var inverse = NativeProof.ReadDatabase(packet.CandidateDbPath);
    NativeProof.RequireDelta(after, inverse, packet.CatalogDelta.Select(c => c with { Before = c.After, After = c.Before }).ToArray());
    NativeProof.SavePrivate("/kavita/config/proof-inverse.json", inverse);
    resultJson = JsonSerializer.Serialize(new { result = "PASS_ACTUAL_NATIVE_SCANNER_PRIVATE_FIXTURE", packet.Phase, packet.JobUid, packet.PodUid,
        nativeSource = FixtureProtocol.SourceCommit, nativeBaseImage = FixtureProtocol.NativeImageDigest,
        tableCount = before.Tables.Count, targetIds = packet.Target, nativeScanStartedAt = started, nativeScanFinishedAt = finished,
        beforeSha256 = NativeProof.Digest(before), afterSha256 = NativeProof.Digest(after), inverseSha256 = NativeProof.Digest(inverse),
        nativeDbBinding, timeZone = TimeZoneInfo.Local.Id, currentTimeZoneOffset = TimeZoneInfo.Local.GetUtcOffset(DateTimeOffset.UtcNow), hostApplicationStarted = false, savedAndCurationRowsExact = true, cleanupRemoved = 0, catalogInverseExact = true, productionWrites = 0, productionAuthorization = false });
    exitCode = 0;
}
catch (Exception error)
{
    // Do not expose vendor errors: they can contain private state or credentials.
    resultJson = JsonSerializer.Serialize(new { result = "REFUSED", stage, kind = error.GetType().Name, outcome = "unknown", productionAuthorization = false });
}
try
{
    if (packet is not null && File.Exists("/kavita/config/proof-before.json"))
        await NativeProof.HandBackEvidence(packet, output, exitCode == 0 ? "passed-private-proof" : "unknown");
}
catch (Exception error)
{
    exitCode = 2;
    resultJson = JsonSerializer.Serialize(new { result = "REFUSED", stage = "private-evidence-ack", kind = error.GetType().Name, outcome = "unknown", productionAuthorization = false });
}
output.WriteLine(resultJson);
alarm?.Dispose();
return exitCode;

static class NativeBindings
{
    public static System.Type Type(string assembly, string name) => Assembly.LoadFrom($"/kavita/{assembly}.dll").GetType(name, true)!;
    public static void Inspect()
    {
        FixtureProtocol.Require(RuntimeInformation.FrameworkDescription == ".NET 10.0.1", "generic apphost runtime differs from native 10.0.1");
        var zone = TimeZoneInfo.FindSystemTimeZoneById(FixtureProtocol.NativeTimeZone);
        FixtureProtocol.Require(Environment.GetEnvironmentVariable("TZ") == FixtureProtocol.NativeTimeZone && TimeZoneInfo.Local.Id == zone.Id && TimeZoneInfo.Local.GetUtcOffset(DateTimeOffset.UtcNow) == zone.GetUtcOffset(DateTimeOffset.UtcNow), "native timezone/offset differs");
        var program = Type("Kavita.Server", "Kavita.Server.Program");
        FixtureProtocol.Require(program.GetMethod("CreateHostBuilder", BindingFlags.NonPublic | BindingFlags.Static)?.ReturnType == typeof(IHostBuilder), "native builder signature differs");
        var scan = Type("Kavita.API", "Kavita.API.Services.Scanner.IScannerService").GetMethod("ScanSeries", new[] { typeof(int), typeof(bool) });
        FixtureProtocol.Require(scan?.ReturnType == typeof(Task), "native scanner signature differs");
        _ = Type("Kavita.Models", "Kavita.Models.Parser.ParsedSeries");
        _ = Type("Microsoft.Data.Sqlite", "Microsoft.Data.Sqlite.SqliteConnection");
        _ = Type("Kavita.Database", "Kavita.Database.DataContext");
        FixtureProtocol.Require(Type("Microsoft.EntityFrameworkCore.Relational", "Microsoft.EntityFrameworkCore.RelationalDatabaseFacadeExtensions").GetMethods().Count(m => m.Name == "GetDbConnection" && m.GetParameters().Length == 1) == 1, "native database facade binding differs");
    }
    public static object ProvePrivateDbContext(IServiceProvider services, string path)
    {
        var context = services.GetRequiredService(Type("Kavita.Database", "Kavita.Database.DataContext"));
        var facade = context.GetType().GetProperty("Database")!.GetValue(context)!;
        var extensions = Type("Microsoft.EntityFrameworkCore.Relational", "Microsoft.EntityFrameworkCore.RelationalDatabaseFacadeExtensions");
        var get = extensions.GetMethods().Single(m => m.Name == "GetDbConnection" && m.GetParameters().Length == 1);
        var connection = (DbConnection)get.Invoke(null, new[] { facade })!;
        FixtureProtocol.Require(connection.GetType().FullName == "Microsoft.Data.Sqlite.SqliteConnection", "native DI context uses another database driver");
        var wasClosed = connection.State == System.Data.ConnectionState.Closed;
        if (wasClosed) connection.Open();
        try
        {
            using var query = connection.CreateCommand(); query.CommandText = "PRAGMA database_list"; query.CommandTimeout = 1;
            using var reader = query.ExecuteReader(); var bound = new List<string>();
            while (reader.Read()) if (reader.GetString(1) != "temp") bound.Add(reader.GetString(2));
            FixtureProtocol.Require(bound.Count == 1 && Path.GetFullPath(bound[0]) == path, "native DI context is outside the candidate clone");
            using var reference = File.OpenRead(path);
            var referenceFd = reference.SafeFileHandle.DangerousGetHandle().ToInt32();
            var inode = File.ReadLines($"/proc/self/fdinfo/{referenceFd}").Single(l => l.StartsWith("ino:\t", StringComparison.Ordinal))[5..];
            var nativeFds = Directory.EnumerateFiles("/proc/self/fd").Where(f => Path.GetFileName(f) != referenceFd.ToString() && new FileInfo(f).LinkTarget == path).ToArray();
            FixtureProtocol.Require(nativeFds.Length > 0 && nativeFds.All(f => File.ReadLines($"/proc/self/fdinfo/{Path.GetFileName(f)}").Single(l => l.StartsWith("ino:\t", StringComparison.Ordinal))[5..] == inode), "native DI clone inode differs");
            return new { path, inode, nativeConnectionType = connection.GetType().FullName, nativeOpenFileCount = nativeFds.Length };
        }
        finally { if (wasClosed) connection.Close(); }
    }
    public static async Task Await(object? value, DateTimeOffset until)
    {
        var task = value as Task ?? throw new InvalidOperationException("native operation did not return a task");
        await task.WaitAsync(until - DateTimeOffset.UtcNow);
    }
}

static class ProcessDeadline
{
    // Telemetry must never precede or block termination (including a full stdout pipe).
    public static Timer Arm(DateTimeOffset until) => new(_ => Environment.Exit(124), null, until - DateTimeOffset.UtcNow, Timeout.InfiniteTimeSpan);
}

sealed record DatabaseSnapshot(string SchemaSha256, Dictionary<string, List<SortedDictionary<string, string>>> Tables);

static class NativeProof
{
    public static string Digest(DatabaseSnapshot snapshot) => FixtureProtocol.HashText(JsonSerializer.Serialize(snapshot));
    public static void RequirePrivateTmpfs(string path)
    {
        var matches = File.ReadLines("/proc/self/mountinfo").Where(line => line.Split(' ')[4] == path).ToArray();
        FixtureProtocol.Require(matches.Length == 1 && matches[0].Split(" - ")[1].Split(' ')[0] == "tmpfs", "fixture must use exact private tmpfs mounts");
    }
    public static void PrivateFile(string path, long maxSize)
    {
        var file = new FileInfo(path);
        FixtureProtocol.Require(file.LinkTarget is null && file.Length > 0 && file.Length <= maxSize, "private input is unsafe or oversized");
        FixtureProtocol.Require(File.GetUnixFileMode(path) == (UnixFileMode.UserRead | UnixFileMode.UserWrite), "private input permissions differ");
        for (var parent = file.Directory; parent is not null; parent = parent.Parent)
            FixtureProtocol.Require(parent.LinkTarget is null, "private input has a symlink ancestor");
    }
    public static void RequireEpubMemberDelta(FixturePacket packet)
    {
        using var original = ZipFile.OpenRead("/fixture-input/original.epub");
        using var candidate = ZipFile.OpenRead(packet.TargetFilePath);
        FixtureProtocol.Require(original.Entries.Count is > 0 and <= 2048 && original.Entries.Select(e => e.FullName).SequenceEqual(candidate.Entries.Select(e => e.FullName)), "EPUB entry identity/order changed");
        FixtureProtocol.Require(original.Entries.Select(e => e.FullName).Distinct().Count() == original.Entries.Count && original.Entries.Sum(e => e.Length) <= 32 * 1024 * 1024 && candidate.Entries.Sum(e => e.Length) <= 32 * 1024 * 1024, "EPUB duplicate/member size bound exceeded");
        var changes = packet.EpubMemberDeltas.ToDictionary(d => d.Member);
        foreach (var before in original.Entries)
        {
            var after = candidate.GetEntry(before.FullName)!;
            FixtureProtocol.Require(before.LastWriteTime == after.LastWriteTime && before.ExternalAttributes == after.ExternalAttributes, "EPUB member metadata changed");
            using var oldStream = before.Open(); using var newStream = after.Open();
            var oldHash = Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(oldStream));
            var newHash = Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(newStream));
            if (changes.Remove(before.FullName, out var allowed))
                FixtureProtocol.Require(oldHash == allowed.BeforeSha256 && newHash == allowed.AfterSha256, "reviewed OPF member bytes differ");
            else FixtureProtocol.Require(oldHash == newHash, "unreviewed EPUB member changed");
        }
        FixtureProtocol.Require(changes.Count == 0, "reviewed OPF member missing");
    }
    public static async Task WaitForPacket(string path, TimeSpan wait)
    {
        if (File.Exists(path)) return;
        using var watcher = new FileSystemWatcher(Path.GetDirectoryName(path)!, Path.GetFileName(path));
        var found = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        watcher.Created += (_, _) => found.TrySetResult();
        watcher.Renamed += (_, _) => found.TrySetResult();
        watcher.EnableRaisingEvents = true;
        if (File.Exists(path)) return;
        await found.Task.WaitAsync(wait);
    }
    static DbConnection Connection(string path, bool readOnly)
    {
        var type = NativeBindings.Type("Microsoft.Data.Sqlite", "Microsoft.Data.Sqlite.SqliteConnection");
        var connection = (DbConnection)Activator.CreateInstance(type)!;
        connection.ConnectionString = $"Data Source={path};Mode={(readOnly ? "ReadOnly" : "ReadWrite")};Pooling=False";
        connection.Open();
        return connection;
    }
    static DbCommand Command(DbConnection db, string sql)
    {
        var command = db.CreateCommand(); command.CommandText = sql; command.CommandTimeout = 1; return command;
    }
    public static DatabaseSnapshot ReadDatabase(string path)
    {
        using var db = Connection(path, true);
        using (var check = Command(db, "PRAGMA quick_check")) FixtureProtocol.Require((string?)check.ExecuteScalar() == "ok", "native database integrity failed");
        using (var fk = Command(db, "PRAGMA foreign_key_check")) using (var reader = fk.ExecuteReader()) FixtureProtocol.Require(!reader.Read(), "native FK check failed");
        var names = new List<string>(); var schema = new List<string>();
        using (var query = Command(db, "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        using (var reader = query.ExecuteReader()) while (reader.Read())
        {
            FixtureProtocol.Require(reader.GetString(0) != "trigger", "unexpected native trigger");
            schema.Add(JsonSerializer.Serialize(Enumerable.Range(0, reader.FieldCount).Select(i => FixtureProtocol.Cell(reader.GetValue(i))).ToArray()));
            if (reader.GetString(0) == "table") names.Add(reader.GetString(1));
        }
        FixtureProtocol.Require(names.Count <= 256, "native schema exceeded table bound");
        var tables = new Dictionary<string, List<SortedDictionary<string, string>>>(); var total = 0;
        foreach (var name in names.Order(StringComparer.Ordinal))
        {
            FixtureProtocol.Require(!name.Contains('"'), "unsafe native table name");
            var rows = new List<SortedDictionary<string, string>>();
            using var query = Command(db, $"SELECT * FROM \"{name}\""); using var reader = query.ExecuteReader();
            while (reader.Read())
            {
                FixtureProtocol.Require(++total <= 100_000, "native row bound exceeded");
                var row = new SortedDictionary<string, string>(StringComparer.Ordinal);
                for (var i = 0; i < reader.FieldCount; i++) row.Add(reader.GetName(i), FixtureProtocol.Cell(reader.GetValue(i)));
                rows.Add(row);
            }
            tables[name] = rows.OrderBy(FixtureProtocol.CanonicalRow, StringComparer.Ordinal).ToList();
        }
        return new(FixtureProtocol.HashText(JsonSerializer.Serialize(schema)), tables);
    }
    static SortedDictionary<string, string> Row(DatabaseSnapshot db, string table, int id) => db.Tables[table].Single(r => r.TryGetValue("Id", out var key) && key == FixtureProtocol.Cell(id));
    static DatabaseSnapshot Copy(DatabaseSnapshot value) => new(value.SchemaSha256,
        value.Tables.ToDictionary(pair => pair.Key, pair => pair.Value.Select(row => new SortedDictionary<string, string>(row, StringComparer.Ordinal)).ToList()));
    static void Sort(DatabaseSnapshot value) { foreach (var name in value.Tables.Keys.ToArray()) value.Tables[name] = value.Tables[name].OrderBy(FixtureProtocol.CanonicalRow, StringComparer.Ordinal).ToList(); }
    public static void RequireDelta(DatabaseSnapshot before, DatabaseSnapshot after, CellChange[] changes)
    {
        var expected = Copy(before);
        foreach (var change in changes)
        {
            var row = Row(expected, change.Table, change.Id);
            FixtureProtocol.Require(row[change.Field] == change.Before, "catalog before cell differs");
            row[change.Field] = change.After;
        }
        Sort(expected);
        FixtureProtocol.Require(Digest(expected) == Digest(after), "undeclared native database delta");
    }
    public static void RequireTarget(DatabaseSnapshot db, FixturePacket packet)
    {
        var t = packet.Target;
        FixtureProtocol.Require(Row(db, "Series", t.Series)["LibraryId"] == FixtureProtocol.Cell(t.Library), "target library changed");
        FixtureProtocol.Require(Row(db, "Volume", t.Volume)["SeriesId"] == FixtureProtocol.Cell(t.Series), "target volume moved");
        FixtureProtocol.Require(Row(db, "Chapter", t.Chapter)["VolumeId"] == FixtureProtocol.Cell(t.Volume), "target chapter moved");
        FixtureProtocol.Require(Row(db, "MangaFile", t.File)["ChapterId"] == FixtureProtocol.Cell(t.Chapter) && Row(db, "MangaFile", t.File)["FilePath"] == FixtureProtocol.Cell(packet.TargetFilePath), "target file moved");
        var volumes = db.Tables["Volume"].Where(r => r["SeriesId"] == FixtureProtocol.Cell(t.Series)).Select(r => r["Id"]).ToHashSet();
        var chapters = db.Tables["Chapter"].Where(r => volumes.Contains(r["VolumeId"])).Select(r => r["Id"]).ToHashSet();
        var files = db.Tables["MangaFile"].Where(r => chapters.Contains(r["ChapterId"])).ToArray();
        FixtureProtocol.Require(volumes.Count == 1 && chapters.Count == 1 && files.Length == 1, "target catalog is not one-volume/chapter/file");
        foreach (var table in new[] { "SeriesMetadata", "ExternalSeriesMetadata" })
            FixtureProtocol.Require(Row(db, table, t.Series)["SeriesId"] == FixtureProtocol.Cell(t.Series), "target metadata ID binding differs");
        var retained = packet.RetainedParsedKeys.Select(k => $"{k.Format}:{k.NormalizedName}").ToHashSet();
        foreach (var row in db.Tables["Series"].Where(r => r["LibraryId"] == FixtureProtocol.Cell(t.Library)))
            FixtureProtocol.Require(retained.Contains($"{row["Format"][4..]}:{row["NormalizedName"][5..]}"), "retained parsed keys omit a current work");
        foreach (var change in packet.CatalogDelta) FixtureProtocol.Require(Row(db, change.Table, change.Id)[change.Field] == change.After, "reviewed key changed");
    }
    public static void RequireScanDelta(DatabaseSnapshot before, DatabaseSnapshot after, FixturePacket packet, DateTimeOffset started, DateTimeOffset finished)
    {
        FixtureProtocol.Require(after.SchemaSha256 == before.SchemaSha256, "scanner changed schema");
        var changes = new List<CellChange>();
        foreach (var allowance in packet.ScanAllowances)
        {
            var actual = Row(after, allowance.Table, allowance.Id)[allowance.Field];
            FixtureProtocol.Require(Row(before, allowance.Table, allowance.Id)[allowance.Field] == allowance.Before, "allowance before cell differs");
            if (allowance.ScanClock)
            {
                FixtureProtocol.Require(actual.StartsWith("text:") && DateTimeOffset.TryParse(actual[5..], out var time) && time >= started.AddSeconds(-1) && time <= finished.AddSeconds(1), "native scan clock outside invocation");
                if (allowance.Table == "Series" && allowance.Field == "LastFolderScanned") FixtureProtocol.Require(actual != allowance.Before, "native scanner returned without target scan evidence");
            }
            else FixtureProtocol.Require(actual == allowance.After, "native explicit scan cell differs");
            changes.Add(new(allowance.Table, allowance.Id, allowance.Field, allowance.Before, actual));
        }
        RequireDelta(before, after, changes.ToArray());
    }
    public static void SavePrivate(string path, DatabaseSnapshot value)
    {
        using var file = new FileStream(path, new FileStreamOptions { Mode = FileMode.CreateNew, Access = FileAccess.Write, UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite });
        JsonSerializer.Serialize(file, value); file.Flush(true);
    }
    public static async Task HandBackEvidence(FixturePacket packet, TextWriter output, string outcome)
    {
        var pins = new List<FilePin>();
        foreach (var name in new[] { "before", "after-build", "after-bind", "after", "inverse" })
        {
            var path = $"/kavita/config/proof-{name}.json";
            if (File.Exists(path))
            {
                PrivateFile(path, 32 * 1024 * 1024);
                pins.Add(new(path, FixtureProtocol.Sha(await File.ReadAllBytesAsync(path))));
            }
        }
        var receipt = new { schema = 1, packet.Phase, packet.JobUid, packet.PodUid, outcome, proofFiles = pins, productionAuthorization = false };
        var receiptBytes = JsonSerializer.SerializeToUtf8Bytes(receipt);
        var receiptPath = "/kavita/config/proof-receipt.json";
        using (var file = new FileStream(receiptPath, new FileStreamOptions { Mode = FileMode.CreateNew, Access = FileAccess.Write, UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite }))
        { file.Write(receiptBytes); file.Flush(true); }
        var receiptSha256 = FixtureProtocol.Sha(receiptBytes);
        // Only aggregate hashes/identities reach logs. The host copies raw proofs privately.
        output.WriteLine(JsonSerializer.Serialize(new { result = "PRIVATE_PROOF_READY", packet.Phase, packet.JobUid, packet.PodUid, receiptSha256, proofFiles = pins, outcome }));
        output.Flush();
        var until = packet.ExpiresAt - DateTimeOffset.UtcNow;
        FixtureProtocol.Require(until > TimeSpan.Zero, "original proof clock expired");
        await WaitForPacket("/fixture-input/evidence-ack.json", until < TimeSpan.FromSeconds(30) ? until : TimeSpan.FromSeconds(30));
        PrivateFile("/fixture-input/evidence-ack.json", 4096);
        var ack = JsonSerializer.Deserialize<EvidenceAck>(await File.ReadAllBytesAsync("/fixture-input/evidence-ack.json")) ?? throw new InvalidOperationException("private durable ACK missing");
        FixtureProtocol.ValidateAck(packet, ack, receiptSha256);
        FixtureProtocol.Require(DateTimeOffset.UtcNow < packet.ExpiresAt, "original proof clock expired after ACK");
    }
    public static void InvertCatalog(FixturePacket packet)
    {
        using var db = Connection(packet.CandidateDbPath, false); using var transaction = db.BeginTransaction();
        foreach (var change in packet.CatalogDelta)
        {
            using var command = Command(db, $"UPDATE {change.Table} SET {change.Field}=@value WHERE Id=@id AND {change.Field}=@old"); command.Transaction = transaction;
            foreach (var (name, value) in new[] { ("@value", Decode(change.Before)), ("@id", (object)change.Id), ("@old", Decode(change.After)) })
            { var parameter = command.CreateParameter(); parameter.ParameterName = name; parameter.Value = value; command.Parameters.Add(parameter); }
            FixtureProtocol.Require(command.ExecuteNonQuery() == 1, "inverse before key changed");
        }
        transaction.Commit();
    }
    static object Decode(string cell) => cell.StartsWith("text:") ? cell[5..] : cell.StartsWith("int:") ? long.Parse(cell[4..], System.Globalization.CultureInfo.InvariantCulture) : cell.StartsWith("real:") ? double.Parse(cell[5..], System.Globalization.CultureInfo.InvariantCulture) : throw new InvalidOperationException("unsupported catalog cell");
    public static void GenericSelfTest()
    {
        var before = new DatabaseSnapshot("synthetic-schema", new Dictionary<string, List<SortedDictionary<string, string>>>
        {
            ["Series"] = [new(StringComparer.Ordinal) { ["Id"] = "int:1", ["Name"] = "text:Before", ["ISBN"] = "text:synthetic", ["Inker"] = "text:synthetic" }],
            ["AppUserProgresses"] = [new(StringComparer.Ordinal) { ["Id"] = "int:2", ["BookScrollId"] = "text:private-fixture-placeholder" }]
        });
        var after = Copy(before); Row(after, "Series", 1)["Name"] = "text:After"; Sort(after);
        var delta = new[] { new CellChange("Series", 1, "Name", "text:Before", "text:After") };
        RequireDelta(before, after, delta);
        var unsafeState = Copy(after); Row(unsafeState, "AppUserProgresses", 2)["BookScrollId"] = "text:changed-fixture";
        var refused = false;
        try { RequireDelta(before, unsafeState, delta); } catch (InvalidOperationException) { refused = true; }
        FixtureProtocol.Require(refused, "unapproved saved-state change accepted");
        var unsafeRemoval = Copy(after); unsafeRemoval.Tables["AppUserProgresses"].Clear();
        refused = false;
        try { RequireDelta(before, unsafeRemoval, delta); } catch (InvalidOperationException) { refused = true; }
        FixtureProtocol.Require(refused, "unapproved saved-state removal accepted");
        var inverse = Copy(after); Row(inverse, "Series", 1)["Name"] = "text:Before"; Sort(inverse);
        RequireDelta(after, inverse, [new("Series", 1, "Name", "text:After", "text:Before")]);
        FixtureProtocol.Require(Digest(before) == Digest(inverse), "generic inverse did not restore exact rows");
    }
}
