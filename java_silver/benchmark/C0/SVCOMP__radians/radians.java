public class radians {

  public static boolean f(double deg) {
    double rad = java.lang.Math.toRadians(deg);
    if (rad >= 0) {
      return true;
    } else {
      return false;
    }
  }
}
