using System.Text.Json;
// Generic ASCII-only vectors, never private native schema or data.
string[] values = ["simple", "quote\"and'single", "<&+>`", "\\\t\n\r\b\f", "\0\v\x7f", "/plain path", "text:CREATE TABLE \"Synthetic\" (\"Value\" TEXT DEFAULT 'x');"];
Console.WriteLine(JsonSerializer.Serialize(values));
Console.WriteLine(JsonSerializer.Serialize(new[] { JsonSerializer.Serialize(values), JsonSerializer.Serialize(new[] { "text:index", "null:" }) }));

// Native File.GetLastWriteTime + Microsoft.Data.Sqlite 10.0.1 value format.
var file = Path.Combine(Path.GetTempPath(), "native-generic-file-time");
File.WriteAllText(file, "generic");
File.SetLastWriteTimeUtc(file, new DateTime(2000, 1, 2, 3, 4, 5, DateTimeKind.Utc).AddTicks(1234567));
if (TimeZoneInfo.Local.Id != "America/New_York") throw new InvalidOperationException("native zone mismatch");
var format = @"yyyy\-MM\-dd HH\:mm\:ss.FFFFFFF";
Console.WriteLine(JsonSerializer.Serialize(new[] { File.GetLastWriteTime(file).ToString(format, System.Globalization.CultureInfo.InvariantCulture), File.GetLastWriteTimeUtc(file).ToString(format, System.Globalization.CultureInfo.InvariantCulture) }));
File.Delete(file);
