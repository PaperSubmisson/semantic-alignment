public class Addition {

    public static int addition(int m, int n) {
        if (n == 0) {
            return m;
        }
        if (n > 0) {
            return addition(m + 1, n - 1);
        } else {
            return addition(m - 1, n + 1);
        }
    }
}