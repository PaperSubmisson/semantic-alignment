class Problem {
  private static final Object[] DEFAULT = {};
  private Object data;

  Problem() {
    this.data = DEFAULT;
  }

  boolean checkInvariant() {
    return data != null;
  }
}

public class lazyloading4 {
  public static boolean f() {
    return new Problem().checkInvariant();
  }
}
