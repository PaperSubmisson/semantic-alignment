public class NegativeArraySizeException2 {
  public static boolean f() {
    try {
      int a[] = new int[-1];
    } catch (Exception exc) {
      return false;
    }
    return true;
  }
}
