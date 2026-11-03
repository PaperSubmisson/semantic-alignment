public class addition01 {

  public static int addition(int m, int n, int c) {
    if (n == 0) {
      return m;
    }

    if (c >= 150) {
      assert c >= 150 && n != 0;
    }

    if (n > 0) {
      return addition(m + 1, n - 1, ++c);
    } else {
      return addition(m - 1, n + 1, ++c);
    }
  }

  public static int f(int m, int n) {
    if (m < 0 || m >= 10000) {
      return -1;
    }
    if (n < 0 || n >= 10000) {
      return -1;
    }
    int c = 0;
    int result = addition(m, n, c);
    return (result == m + n) ? 1 : 0;
  }
}
