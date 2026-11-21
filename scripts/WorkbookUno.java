import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.ArrayList;
import java.util.List;
import com.sun.star.beans.*;
import com.sun.star.bridge.XUnoUrlResolver;
import com.sun.star.comp.helper.Bootstrap;
import com.sun.star.frame.*;
import com.sun.star.lang.XComponent;
import com.sun.star.sheet.*;
import com.sun.star.table.*;
import com.sun.star.text.XText;
import com.sun.star.uno.*;

/** Apply explicit cell/range edits to an existing workbook through LibreOffice. */
public class WorkbookUno {
    static <T> T q(Class<T> type, Object object) { return UnoRuntime.queryInterface(type, object); }
    static PropertyValue prop(String name, Object value) {
        PropertyValue p = new PropertyValue(); p.Name = name; p.Value = value; return p;
    }
    public static void main(String[] args) {
        try {
            run(args);
        } catch (Throwable error) {
            error.printStackTrace();
            System.exit(1);
        }
    }
    private static void run(String[] args) throws java.lang.Exception {
        XComponentContext local = Bootstrap.createInitialComponentContext(null);
        XUnoUrlResolver resolver = q(XUnoUrlResolver.class,
                local.getServiceManager().createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local));
        XComponentContext context = q(XComponentContext.class, resolver.resolve(
                "uno:socket,host=localhost,port=" + System.getenv().getOrDefault("SYNTHIFHIR_UNO_PORT", "20152")
                        + ";urp;StarOffice.ComponentContext"));
        XComponentLoader loader = q(XComponentLoader.class,
                context.getServiceManager().createInstanceWithContext("com.sun.star.frame.Desktop", context));
        XComponent doc = loader.loadComponentFromURL(Path.of(args[0]).toUri().toString(), "_blank", 0,
                new PropertyValue[] {prop("Hidden", true), prop("UpdateDocMode", (short) 0)});
        try (java.io.BufferedReader commands = Files.newBufferedReader(Path.of(args[1]), StandardCharsets.UTF_8)) {
            XSpreadsheetDocument book = q(XSpreadsheetDocument.class, doc);
            XPropertySet settings = q(XPropertySet.class, doc);
            Object undoEnabled = settings.getPropertyValue("IsUndoEnabled");
            // The generated document is disposable on failure; restore before saving.
            settings.setPropertyValue("IsUndoEnabled", false);
            String pending = null;
            while (true) {
                String line = pending != null ? pending : commands.readLine();
                pending = null;
                if (line == null) break;
                if (line.isBlank()) continue;
                String[] a = line.split("\t", -1);
                if (a[0].equals("removeSheet")) {
                    book.getSheets().removeByName(a[1]);
                    continue;
                }
                if (a[0].equals("moveSheet")) {
                    book.getSheets().moveByName(a[1], Short.parseShort(a[2]));
                    continue;
                }
                if (a[0].equals("copySheet")) {
                    book.getSheets().copyByName(a[2], a[1], (short) book.getSheets().getElementNames().length);
                    continue;
                }
                XSpreadsheet sheet = q(XSpreadsheet.class, book.getSheets().getByName(a[1]));
                if (a[0].equals("reuseValidation")) {
                    String[] ranges = a[2].trim().split("\\s+");
                    Object validation = q(XPropertySet.class,
                            sheet.getCellRangeByName(ranges[0]).getCellByPosition(0, 0)).getPropertyValue("Validation");
                    for (String range : ranges) {
                        q(XPropertySet.class, sheet.getCellRangeByName(range)).setPropertyValue("Validation", validation);
                    }
                } else if (a[0].equals("row")) {
                    int row = Integer.parseInt(a[2].substring(1)) - 1;
                    List<Object[]> values = new ArrayList<>();
                    values.add(rowValues(a));
                    // Bound each UNO payload; preserve operation order and range boundaries.
                    while (values.size() < 512 && (pending = commands.readLine()) != null) {
                        String[] next = pending.split("\t", -1);
                        if (next.length != a.length || !next[0].equals("row") || !next[1].equals(a[1])
                                || Integer.parseInt(next[2].substring(1)) - 1 != row + values.size()) break;
                        values.add(rowValues(next));
                        pending = null;
                    }
                    q(XCellRangeData.class, sheet.getCellRangeByPosition(0, row, a.length - 4,
                            row + values.size() - 1)).setDataArray(values.toArray(new Object[0][]));
                } else if (a[0].equals("insert")) {
                    q(XColumnRowRange.class, sheet).getColumns().insertByIndex(Integer.parseInt(a[2]), Integer.parseInt(a[3]));
                } else if (a[0].equals("removeRows")) {
                    q(XColumnRowRange.class, sheet).getRows().removeByIndex(Integer.parseInt(a[2]), Integer.parseInt(a[3]));
                } else if (a[0].equals("removeColumns")) {
                    q(XColumnRowRange.class, sheet).getColumns().removeByIndex(Integer.parseInt(a[2]), Integer.parseInt(a[3]));
                } else if (a[0].equals("copy")) {
                    CellRangeAddress source = q(XCellRangeAddressable.class, sheet.getCellRangeByName(a[2])).getRangeAddress();
                    CellAddress target = q(XCellAddressable.class, sheet.getCellRangeByName(a[3]).getCellByPosition(0, 0)).getCellAddress();
                    XCellRangeMovement movement = q(XCellRangeMovement.class, sheet);
                    String column = columnName(target.Column);
                    // Reuse addresses for adjacent copies, preserving each copy and its order.
                    while (true) {
                        movement.copyRange(target, source);
                        pending = commands.readLine();
                        if (pending == null) break;
                        String[] next = pending.split("\t", -1);
                        if (next.length != 4 || !next[0].equals("copy") || !next[1].equals(a[1])
                                || !next[2].equals(a[2]) || !next[3].equals(column + (target.Row + 2))) break;
                        target.Row++;
                        pending = null;
                    }
                } else if (a[0].equals("clear")) {
                    q(XSheetOperation.class, sheet.getCellRangeByName(a[2])).clearContents(1 | 2 | 4 | 16);
                } else if (a[0].equals("set")) {
                    q(XText.class, sheet.getCellRangeByName(a[2]).getCellByPosition(0, 0)).setString(
                            new String(Base64.getDecoder().decode(a[3]), StandardCharsets.UTF_8));
                } else if (a[0].equals("formula")) {
                    sheet.getCellRangeByName(a[2]).getCellByPosition(0, 0).setFormula(
                            new String(Base64.getDecoder().decode(a[3]), StandardCharsets.UTF_8));
                } else if (a[0].equals("noValidation")) {
                    XPropertySet cells = q(XPropertySet.class, sheet.getCellRangeByName(a[2]));
                    Object validation = cells.getPropertyValue("Validation");
                    q(XPropertySet.class, validation).setPropertyValue("Type", ValidationType.ANY);
                    cells.setPropertyValue("Validation", validation);
                } else if (a[0].equals("validation")) {
                    XPropertySet cells = q(XPropertySet.class, sheet.getCellRangeByName(a[2]));
                    Object validation = cells.getPropertyValue("Validation");
                    XPropertySet props = q(XPropertySet.class, validation);
                    props.setPropertyValue("Type", ValidationType.LIST);
                    props.setPropertyValue("IgnoreBlankCells", true);
                    props.setPropertyValue("ShowErrorMessage", Boolean.parseBoolean(a[4]));
                    XSheetCondition condition = q(XSheetCondition.class, validation);
                    condition.setSourcePosition(q(XCellAddressable.class,
                            sheet.getCellRangeByName(a[2]).getCellByPosition(0, 0)).getCellAddress());
                    condition.setFormula1(a[3]);
                    cells.setPropertyValue("Validation", validation);
                } else if (a[0].equals("text")) {
                    XPropertySet cells = q(XPropertySet.class, sheet.getCellRangeByName(a[2]));
                    com.sun.star.util.XNumberFormats formats = q(com.sun.star.util.XNumberFormatsSupplier.class, doc).getNumberFormats();
                    int textFormat = formats.queryKey("@", new com.sun.star.lang.Locale(), true);
                    if (textFormat < 0) textFormat = formats.addNew("@", new com.sun.star.lang.Locale());
                    cells.setPropertyValue("NumberFormat", textFormat);
                    cells.setPropertyValue("CharFontName", "Calibri");
                } else if (a[0].equals("rowHeight")) {
                    XPropertySet rows = q(XPropertySet.class,
                            q(XColumnRowRange.class, sheet.getCellRangeByName(a[2])).getRows());
                    rows.setPropertyValue("Height", (int) Math.round(Double.parseDouble(a[3]) * 2540 / 72));
                } else if (a[0].equals("background")) {
                    q(XPropertySet.class, sheet.getCellRangeByName(a[2])).setPropertyValue("CellBackColor", Integer.parseInt(a[3], 16));
                } else if (a[0].equals("noWrap")) {
                    q(XPropertySet.class, sheet.getCellRangeByName(a[2])).setPropertyValue("IsTextWrapped", false);
                } else if (a[0].equals("width")) {
                    q(XPropertySet.class, q(XColumnRowRange.class, sheet).getColumns().getByIndex(Integer.parseInt(a[2])))
                            .setPropertyValue("Width", Integer.parseInt(a[3]));
                }
            }
            settings.setPropertyValue("IsUndoEnabled", undoEnabled);
            if (args.length >= 3) {
                XSpreadsheet diagnosis = q(XSpreadsheet.class, book.getSheets().getByName(args.length >= 4 ? args[3] : "Diagnose"));
                q(XStorable.class, doc).storeToURL(Path.of(args[2]).toUri().toString(),
                        new PropertyValue[] {prop("FilterName", "calc_pdf_Export"), prop("Overwrite", true),
                                prop("FilterData", new PropertyValue[] {prop("Selection", diagnosis.getCellRangeByName(args.length >= 5 ? args[4] : "A1:N6")),
                                        prop("SinglePageSheets", true)})});
            } else {
                q(XStorable.class, doc).store();
            }
        } finally {
            doc.dispose();
        }
        // UNO connection threads otherwise keep the standalone helper alive.
        System.exit(0);
    }

    private static String columnName(int column) {
        StringBuilder name = new StringBuilder();
        for (int value = column + 1; value > 0; value = (value - 1) / 26)
            name.append((char) ('A' + (value - 1) % 26));
        return name.reverse().toString();
    }

    private static Object[] rowValues(String[] operation) {
        Object[] values = new Object[operation.length - 3];
        for (int i = 3; i < operation.length; i++) values[i - 3] =
                new String(Base64.getDecoder().decode(operation[i]), StandardCharsets.UTF_8);
        return values;
    }
}
