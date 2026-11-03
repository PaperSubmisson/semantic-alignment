public class ArithmeticException6 {

  public static boolean arithmeticException6(int denom) {
    try {
      int j = 10 / denom;
    } catch (ArithmeticException exc) {
      return false;
    }
    return true;
  }

}
