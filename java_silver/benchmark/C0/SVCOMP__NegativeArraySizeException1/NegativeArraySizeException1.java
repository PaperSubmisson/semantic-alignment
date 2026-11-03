public class NegativeArraySizeException1 {
  public static boolean f() {
    try {
      int a[] = new int[-1];
    } catch (NegativeArraySizeException exc) {
      return false;
    }
    return true;
  }
}
