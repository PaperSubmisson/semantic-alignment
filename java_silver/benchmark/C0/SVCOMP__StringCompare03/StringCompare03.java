public class StringCompare03 {
  public static boolean f(String s3, String s4) {
    // test regionMatches (ignore case)
    return !s3.regionMatches(true, 0, s4, 0, 5);
  }
}
