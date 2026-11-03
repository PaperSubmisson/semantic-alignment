public class StringCompare02 {
  public static boolean f(String s3, String s4) {
    // test regionMatches (case sensitive)
    return s3.regionMatches(0, s4, 0, 5);
  }
}
