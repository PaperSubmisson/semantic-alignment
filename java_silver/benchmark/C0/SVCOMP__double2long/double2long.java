public class double2long {

  public static double bar(long l) {
    assert l >= Long.MIN_VALUE && l <= Long.MAX_VALUE;
    return (double) l;
  }

  public static double foo(double x) {
    if (x < 0.0) {
      x *= 10.0;
    } else {
      x /= 10.0;
    }
    long l = (long) x;
    return bar(l);
  }

}
