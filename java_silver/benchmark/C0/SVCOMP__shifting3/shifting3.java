public class shifting3 {

  public static int f(int i) {
    if (i < 1 || i > 100) {
      return -1;
    }

    return ((1L << i) != 1L) ? 1 : 0;
  }
}
