package local.manama;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.TimeZone;

/** Uses the device's actual IAlarmManager interface, not a guessed Binder code. */
public final class TzControl {
    private TzControl() { }

    public static void main(String[] args) {
        try {
            if (args.length == 1 && "read".equals(args[0])) {
                report();
                return;
            }
            if (args.length != 2 || !("set".equals(args[0]) || "validate".equals(args[0]))
                    || !validZone(args[1])) {
                throw new IllegalArgumentException("Expected read, validate ID, or set ID");
            }
            if ("validate".equals(args[0])) {
                System.out.println("MANAMA_TZ_VALID=" + args[1]);
                return;
            }
            Class<?> binderClass = Class.forName("android.os.IBinder");
            Class<?> managerClass = Class.forName("android.os.ServiceManager");
            Object binder = managerClass.getMethod("getService", String.class).invoke(null, "alarm");
            if (binder == null) {
                throw new IllegalStateException("Alarm service unavailable");
            }
            Class<?> stubClass = Class.forName("android.app.IAlarmManager$Stub");
            Object manager = stubClass.getMethod("asInterface", binderClass).invoke(null, binder);
            if (manager == null) {
                throw new IllegalStateException("Alarm service interface unavailable");
            }
            Method setTimeZone = Class.forName("android.app.IAlarmManager")
                    .getMethod("setTimeZone", String.class);
            setTimeZone.invoke(manager, args[1]);
            TimeZone.setDefault(null);
            String actual = TimeZone.getDefault().getID();
            if (!args[1].equals(actual)) {
                throw new IllegalStateException("Timezone service returned but Java sees " + actual);
            }
            report();
        } catch (Throwable problem) {
            if (problem instanceof InvocationTargetException
                    && ((InvocationTargetException) problem).getCause() != null) {
                problem = ((InvocationTargetException) problem).getCause();
            }
            System.err.println("MANAMA_TZ_ERROR: " + problem);
            System.exit(1);
        }
    }

    private static boolean validZone(String id) {
        return Arrays.asList(TimeZone.getAvailableIDs()).contains(id)
                || (id.startsWith("GMT") && id.equals(TimeZone.getTimeZone(id).getID()));
    }

    private static void report() {
        TimeZone.setDefault(null);
        System.out.println("MANAMA_TZ_DEFAULT=" + TimeZone.getDefault().getID());
    }
}
