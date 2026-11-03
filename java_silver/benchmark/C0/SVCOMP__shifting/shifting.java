public class shifting {

  public static int f(int i) {
    if (i < 0 || i > 100) {
      return -1;
    }

    if ((1L << i) > Integer.MAX_VALUE) {
      return 0;
    }
    return 1;
  }
}
