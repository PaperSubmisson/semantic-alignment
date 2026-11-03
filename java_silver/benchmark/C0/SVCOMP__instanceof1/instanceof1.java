/*
 * Origin of the benchmark:
 *     license: 4-clause BSD (see /java/jbmc-regression/LICENSE)
 *     repo: https://github.com/diffblue/cbmc.git
 *     branch: develop
 *     directory: regression/cbmc-java/instanceof1
 * The benchmark was taken from the repo: 24 January 2018
 */
class instanceof1 {
  public static boolean f(String[] args) {
    return args instanceof Object;
  }

  public static void main(String[] args) {
    f(args);
  }
}