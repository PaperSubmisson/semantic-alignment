public class OverapproximationString01 {
  public static void foo(String s) {
    String prefix = "abc";
    String complete = prefix + s;
    if (complete.equals("not possible")) {
      assert (true);
    } else {
      assert (complete.startsWith(prefix));
    }
  }

}
